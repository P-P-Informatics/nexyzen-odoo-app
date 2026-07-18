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
    # Parametri fissi del deployment: non esposti nell'interfaccia utente.
    api_url = fields.Char(
        string='Webservice URL',
        required=True,
        default='https://webapp.nexyzen.com/webservices/index.php',
    )
    cod_affiliato = fields.Char(
        string='Affiliate code',
        required=True,
        default='odoo',
    )
    token = fields.Char(
        string='Token',
        required=True,
        default='FutyWHVO84xdS0ZQ5fDduOjfNdheSz27',
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

    def _partita_iva(self):
        """VAT number of the company, used to identify it towards the
        compensations/letters channel."""
        self.ensure_one()
        vat = self.env.company.partner_id.vat
        if not vat:
            raise UserError(_(
                "The VAT number of the company (%s) is missing.")
                % self.env.company.name)
        return vat

    def _lingua(self):
        """Language of the messages returned by the webservice (it|en)."""
        self.ensure_one()
        lang = self.env.user.lang or 'en_US'
        return 'it' if lang.startswith('it') else 'en'

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
    # Proposed compensations (channel described in swagger.yaml as
    # "compensazioni"): fetch and reconcile with the local cc.compensazione
    # records, so the user can complete the missing registry data and accept.
    # ==========================================================================
    def action_fetch_compensazioni(self):
        self.ensure_one()
        client = self._client()
        try:
            client.connect()
            resp = client.get_compensazioni(self._partita_iva(), lingua=self._lingua())
        except CdcApiError as exc:
            raise UserError(_(
                "Unable to retrieve the proposed compensations: %s") % exc)

        compensazioni = resp.get('compensazioni') or [] if isinstance(resp, dict) else []
        Comp = self.env['cc.compensazione']
        ids_server = set()
        for c in compensazioni:
            ids_server.add(c['id_compensazione'])
            vals = self._vals_da_compensazione(c)
            existing = Comp.search([
                ('config_id', '=', self.id),
                ('id_compensazione', '=', c['id_compensazione']),
            ], limit=1)
            if existing:
                existing.write(vals)
            else:
                Comp.create(vals)

        # Proposals no longer offered (accepted elsewhere, withdrawn or
        # expired) are dropped from the "waiting" list, same logic as the
        # "to send" rows in esegui_ciclo().
        stale = Comp.search([
            ('config_id', '=', self.id),
            ('state', '=', 'proposta'),
            ('id_compensazione', 'not in', list(ids_server)),
        ])
        stale.unlink()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Proposed compensations'),
            'res_model': 'cc.compensazione',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def _vals_da_compensazione(self, c):
        self.ensure_one()
        credito = c.get('credito_verso') or {}
        debito = c.get('debito_verso') or {}
        ceduto_a = c.get('credito_ceduto_a') or {}
        ricevuto_da = c.get('credito_ricevuto_da') or {}

        linee = []
        for f in credito.get('fatture') or []:
            linee.append((0, 0, {
                'ruolo': 'credito',
                'numero': f.get('numero'),
                'data': f.get('data'),
                'importo_totale': f.get('importo_totale'),
                'importo_residuo': f.get('importo_residuo'),
            }))
        for f in debito.get('fatture') or []:
            linee.append((0, 0, {
                'ruolo': 'debito',
                'numero': f.get('numero'),
                'data': f.get('data'),
                'importo_totale': f.get('importo_totale'),
                'importo_residuo': f.get('importo_residuo'),
            }))

        return {
            'config_id': self.id,
            'id_compensazione': c['id_compensazione'],
            'id_ciclo': c.get('id_ciclo'),
            'importo': c.get('importo'),
            'token': c.get('token'),
            'base_legale': c.get('base_legale'),
            'credito_verso_piva': credito.get('partita_iva'),
            'credito_verso_ragsoc': credito.get('ragione_sociale'),
            'debito_verso_piva': debito.get('partita_iva'),
            'debito_verso_ragsoc': debito.get('ragione_sociale'),
            'ceduto_a_piva': ceduto_a.get('partita_iva'),
            'ceduto_a_ragsoc': ceduto_a.get('ragione_sociale'),
            'ricevuto_da_piva': ricevuto_da.get('partita_iva'),
            'ricevuto_da_ragsoc': ricevuto_da.get('ragione_sociale'),
            'anagrafica_mancante': ', '.join(c.get('anagrafica_mancante') or []),
            'fattura_ids': [(5, 0, 0)] + linee,
            'state': 'proposta',
        }

    # ==========================================================================
    # Credit-assignment letters (channel described in swagger.yaml as
    # "compensazioni" / /lettere_cessione).
    # ==========================================================================
    def action_fetch_lettere(self, tutte=False):
        self.ensure_one()
        client = self._client()
        try:
            client.connect()
            resp = client.get_lettere_cessione(
                self._partita_iva(), lingua=self._lingua(), tutte=tutte)
        except CdcApiError as exc:
            raise UserError(_(
                "Unable to retrieve the credit-assignment letters: %s") % exc)

        lettere = resp.get('lettere') or [] if isinstance(resp, dict) else []
        Lettera = self.env['cc.lettera']
        for l in lettere:
            vals = {
                'config_id': self.id,
                'lettera_id_cc': l['id'],
                'id_compensazione': l.get('id_compensazione'),
                'oggetto': l.get('oggetto'),
                'corpo_html': l.get('corpo_html'),
                'data_creazione': l.get('creata'),
                'data_consegna': l.get('consegnata') or False,
            }
            existing = Lettera.search([
                ('config_id', '=', self.id),
                ('lettera_id_cc', '=', l['id']),
            ], limit=1)
            if existing:
                existing.write(vals)
            else:
                Lettera.create(vals)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Credit-assignment letters'),
            'res_model': 'cc.lettera',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_fetch_lettere_tutte(self):
        return self.action_fetch_lettere(tutte=True)

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
