# -*- coding: utf-8 -*-
import logging
from datetime import date

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .cc_api import CdcClient, CdcApiError

_logger = logging.getLogger(__name__)

# Python weekday() (0=Monday) -> boolean field name.
_WEEKDAY_FIELDS = {
    0: 'giorno_lun',
    1: 'giorno_mar',
    2: 'giorno_mer',
    3: 'giorno_gio',
    4: 'giorno_ven',
    5: 'giorno_sab',
    # 6 = Sunday: never (not foreseen by the specification)
}


class CcConfig(models.Model):
    _name = 'cc.config'
    _description = 'Nexyzen Settings'

    name = fields.Char(default='Nexyzen Settings', readonly=True)

    # --- Webservice connection -----------------------------------------------
    api_url = fields.Char(
        string='Webservice URL',
        required=True,
        default='https://webapp.nexyzen.com/webservices/index.php',
        help="Address of the Nexyzen webservice "
             "(points to .../webservices/index.php).",
    )
    cod_affiliato = fields.Char(
        string='Affiliate code',
        required=True,
        default='odoo',
        help="Affiliate code provided by Nexyzen "
             "(commerciale@cameracompensazione.it). The default 'odoo' account "
             "runs in test mode (invoices are validated but not persisted); "
             "enter your own credentials to send for real.",
    )
    token = fields.Char(
        string='Token',
        required=True,
        default='FutyWHVO84xdS0ZQ5fDduOjfNdheSz27',
        help="Access token provided by Nexyzen. Replace with your own to send "
             "invoices for real.",
    )
    email_proponente = fields.Char(
        string='Notification email',
        help="Email to which Nexyzen sends confirmations/notifications "
             "(optional).",
    )
    cellulare_proponente = fields.Char(
        string='Notification mobile',
        help="Mobile number for SMS notifications (optional).",
    )

    # --- Invoice selection criteria ------------------------------------------
    invia_vendite = fields.Boolean(
        string='Customer invoices (active)', default=True)
    invia_acquisti = fields.Boolean(
        string='Vendor bills (passive)', default=True)

    giorni_min = fields.Integer(
        string='From (days to due date)',
        default=-3650,
        help="Consider invoices whose due date is at least this many days "
             "away. Negative values = already overdue. "
             "E.g. -3650 includes every overdue invoice.",
    )
    giorni_max = fields.Integer(
        string='To (days to due date)',
        default=30,
        help="Consider invoices whose due date is at most this many days "
             "away. E.g. 30 = due within 30 days.",
    )
    importo_min = fields.Float(
        string='Minimum amount',
        default=0.0,
        help="Only send invoices with a residual amount >= this value.",
    )
    importo_max = fields.Float(
        string='Maximum amount',
        default=0.0,
        help="Only send invoices with a residual amount <= this value. "
             "0 = no upper limit.",
    )
    partner_ids = fields.Many2many(
        'res.partner',
        string='Only these customers/vendors',
        help="If empty, all customers/vendors are considered.",
    )

    # --- Sending mode --------------------------------------------------------
    modalita = fields.Selection(
        [('auto', 'Automatic'),
         ('manuale', 'With manual approval')],
        string='Sending mode',
        default='manuale',
        required=True,
        help="Automatic: invoices are sent immediately. "
             "With manual approval: a list is prepared for you to review "
             "before sending.",
    )

    # --- Weekdays on which to send -------------------------------------------
    giorno_lun = fields.Boolean(string='Monday', default=True)
    giorno_mar = fields.Boolean(string='Tuesday', default=True)
    giorno_mer = fields.Boolean(string='Wednesday', default=True)
    giorno_gio = fields.Boolean(string='Thursday', default=True)
    giorno_ven = fields.Boolean(string='Friday', default=True)
    giorno_sab = fields.Boolean(string='Saturday', default=False)

    # ==========================================================================
    # Singleton helper
    # ==========================================================================
    @api.model
    def get_config(self):
        """Return the configuration record (creating it if missing)."""
        config = self.search([], limit=1)
        if not config:
            config = self.create({})
        return config

    def action_apri_form(self):
        """Open the form of the single configuration record."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Nexyzen Settings'),
            'res_model': 'cc.config',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ==========================================================================
    # Invoice selection
    # ==========================================================================
    def _tipi_move(self):
        self.ensure_one()
        tipi = []
        if self.invia_vendite:
            tipi.append('out_invoice')
        if self.invia_acquisti:
            tipi.append('in_invoice')
        return tipi

    def _select_moves(self):
        """Return the unpaid account.move records matching the criteria."""
        self.ensure_one()
        tipi = self._tipi_move()
        if not tipi:
            return self.env['account.move'].browse()

        domain = [
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
            ('move_type', 'in', tipi),
            ('company_id', '=', self.env.company.id),
        ]
        if self.partner_ids:
            domain.append(
                ('commercial_partner_id', 'in', self.partner_ids.ids))

        moves = self.env['account.move'].search(domain)
        today = fields.Date.context_today(self)
        selezionate = moves.filtered(
            lambda m: self._move_rispetta_criteri(m, today))
        return selezionate

    def _move_rispetta_criteri(self, move, today):
        self.ensure_one()
        # Residual amount (always positive on our side).
        residuo = abs(move.amount_residual)
        if residuo <= 0:
            return False
        if residuo < self.importo_min:
            return False
        if self.importo_max > 0 and residuo > self.importo_max:
            return False

        # Days to due date. If the due date is missing, the invoice date is
        # used as reference.
        scadenza = move.invoice_date_due or move.invoice_date
        if not scadenza:
            return False
        offset = (scadenza - today).days
        if offset < self.giorni_min or offset > self.giorni_max:
            return False
        return True

    # ==========================================================================
    # Building the webservice payload
    # ==========================================================================
    def _dati_da_move(self, move, importo_residuo=None):
        """Build the `dati` dict for op=ins_manuale from an account.move.

        :param importo_residuo: if set, use this amount to offset instead of the
            move residual (allows manual override).
        Raises UserError with a clear message if indispensable data is missing.
        """
        self.ensure_one()
        is_vendita = move.move_type == 'out_invoice'
        tipo = 'v' if is_vendita else 'a'

        piva_azienda = move.company_id.partner_id.vat
        piva_partner = move.commercial_partner_id.vat

        if not piva_azienda:
            raise UserError(_(
                "The VAT number of the company (%s) is missing.")
                % move.company_id.name)
        if not piva_partner:
            raise UserError(_(
                "The VAT number of %s is missing.")
                % move.commercial_partner_id.name)

        if is_vendita:
            piva_creditore, piva_debitore = piva_azienda, piva_partner
        else:
            piva_creditore, piva_debitore = piva_partner, piva_azienda

        # Invoice number: for vendor bills the supplier number is in `ref`.
        numero = move.ref if (not is_vendita and move.ref) else move.name

        data_fattura = move.invoice_date
        if not data_fattura:
            raise UserError(_(
                "The date of invoice %s is missing.") % (move.name or move.id))

        residuo = (importo_residuo if importo_residuo is not None
                   else abs(move.amount_residual))
        dati = {
            'tipo_fattura': tipo,
            'partita_iva_creditore': piva_creditore,
            'partita_iva_debitore': piva_debitore,
            'data_fattura': data_fattura.strftime('%Y-%m-%d'),
            'numero_fattura': numero or str(move.id),
            'importo_totale': round(abs(move.amount_total), 2),
            'importo_residuo': round(abs(residuo), 2),
        }
        if self.email_proponente:
            dati['email_proponente'] = self.email_proponente
        if self.cellulare_proponente:
            dati['cellulare_proponente'] = self.cellulare_proponente
        return dati

    # ==========================================================================
    # Client
    # ==========================================================================
    def _client(self):
        self.ensure_one()
        return CdcClient(self.api_url, self.cod_affiliato, self.token)

    def action_test_connessione(self):
        """Button: check that the credentials work."""
        self.ensure_one()
        try:
            self._client().connect()
        except CdcApiError as exc:
            raise UserError(_("Connection failed: %s") % exc)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Connection successful"),
                'message': _("The Nexyzen webservice responded correctly."),
                'type': 'success',
                'sticky': False,
            },
        }

    # ==========================================================================
    # Cycle execution
    # ==========================================================================
    def _oggi_e_giorno_invio(self):
        self.ensure_one()
        weekday = date.today().weekday()
        campo = _WEEKDAY_FIELDS.get(weekday)
        return bool(campo and self[campo])

    def esegui_ciclo(self, forza=False):
        """Select the invoices and, depending on the mode, send them or prepare
        them for approval.

        :param forza: if True, ignore the weekday check (used by the
                      "Run now" button).
        :return: the cc.invio records created/updated.
        """
        self.ensure_one()
        if not forza and not self._oggi_e_giorno_invio():
            _logger.info("Nexyzen: today is not a sending day, cycle skipped.")
            return self.env['cc.invio'].browse()

        moves = self._select_moves()
        Invio = self.env['cc.invio']

        # Remove the "to send" rows from the previous cycle that were not
        # handled: the offsetting list is always recomputed from scratch.
        Invio.search([('config_id', '=', self.id),
                      ('state', '=', 'da_inviare')]).unlink()

        invii = Invio
        for move in moves:
            invii |= Invio.create({
                'config_id': self.id,
                'move_id': move.id,
                'state': 'da_inviare',
            })

        if self.modalita == 'auto':
            invii.action_invia()

        _logger.info("Nexyzen: cycle executed, %s invoices selected (mode %s).",
                     len(invii), self.modalita)
        return invii

    def action_esegui_adesso(self):
        """Button: run the cycle now, ignoring the weekday."""
        self.ensure_one()
        invii = self.esegui_ciclo(forza=True)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoices to offset'),
            'res_model': 'cc.invio',
            'view_mode': 'list,form',
            'domain': [('id', 'in', invii.ids)],
            'target': 'current',
        }

    # ==========================================================================
    # Daily cron
    # ==========================================================================
    @api.model
    def cron_esegui_giornaliero(self):
        """Cron entry point: run the cycle for each company."""
        config = self.get_config()
        config.esegui_ciclo(forza=False)
