# -*- coding: utf-8 -*-
"""Compensations proposed by the Nexyzen clearing engine, waiting for the
company's acceptance (channel described in swagger.yaml as "compensazioni":
/compensazioni, /accetta_compensazione).
"""
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .cc_api import CdcApiError

_logger = logging.getLogger(__name__)

# swagger AnagraficaCompensazione field -> local field name.
_ANAGRAFICA_FIELDS = [
    'ragione_sociale', 'indirizzo', 'cap', 'comune', 'provincia', 'nazione',
    'email', 'telefono', 'nome_legale_rappresentante',
    'cognome_legale_rappresentante', 'cf_legale_rappresentante',
    'qualita_legale_rappresentante', 'luogo_nascita_legale_rappresentante',
    'data_nascita_legale_rappresentante', 'email_legale_rappresentante',
]


class CcCompensazioneFattura(models.Model):
    _name = 'cc.compensazione.fattura'
    _description = 'Invoice referenced by a proposed compensation'
    _order = 'ruolo, data'

    compensazione_id = fields.Many2one(
        'cc.compensazione', string='Compensation',
        required=True, ondelete='cascade')
    ruolo = fields.Selection(
        [('credito', 'Receivable ceded'), ('debito', 'Payable offset')],
        string='Role', required=True)
    numero = fields.Char(string='Number')
    data = fields.Char(string='Date')
    currency_id = fields.Many2one(
        related='compensazione_id.currency_id', store=True)
    importo_totale = fields.Monetary(
        string='Total amount', currency_field='currency_id')
    importo_residuo = fields.Monetary(
        string='Open amount', currency_field='currency_id')


class CcCompensazione(models.Model):
    _name = 'cc.compensazione'
    _description = 'Compensation proposed by Nexyzen'
    _order = 'create_date desc, id desc'
    _rec_name = 'id_compensazione'

    config_id = fields.Many2one('cc.config', string='Configuration',
                                required=True, ondelete='cascade')

    # --- Data as returned by /compensazioni ----------------------------------
    id_compensazione = fields.Integer(
        string='Compensation ID', required=True, index=True)
    id_ciclo = fields.Integer(string='Cycle ID')
    importo = fields.Monetary(string='Amount', currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id)
    token = fields.Char(string='One-time token')
    base_legale = fields.Text(string='Legal basis')

    credito_verso_piva = fields.Char(string='Receivable towards (VAT)')
    credito_verso_ragsoc = fields.Char(string='Receivable towards (name)')
    debito_verso_piva = fields.Char(string='Payable towards (VAT)')
    debito_verso_ragsoc = fields.Char(string='Payable towards (name)')
    ceduto_a_piva = fields.Char(string='Credit assigned to (VAT)')
    ceduto_a_ragsoc = fields.Char(string='Credit assigned to (name)')
    ricevuto_da_piva = fields.Char(string='Credit received from (VAT)')
    ricevuto_da_ragsoc = fields.Char(string='Credit received from (name)')

    fattura_ids = fields.One2many(
        'cc.compensazione.fattura', 'compensazione_id', string='Invoices')

    # --- Missing registry data, to complete before accepting -----------------
    anagrafica_mancante = fields.Char(
        string='Missing registry fields',
        help="Comma-separated field codes returned by the engine "
             "(anagrafica_mancante); fill in the matching fields below "
             "before accepting.")
    richiede_anagrafica = fields.Boolean(
        string='Registry data needed',
        compute='_compute_richiede_anagrafica', store=True)

    anagrafica_ragione_sociale = fields.Char(string='Company name')
    anagrafica_indirizzo = fields.Char(string='Address')
    anagrafica_cap = fields.Char(string='ZIP code')
    anagrafica_comune = fields.Char(string='City')
    anagrafica_provincia = fields.Char(string='Province')
    anagrafica_nazione = fields.Char(string='Country')
    anagrafica_email = fields.Char(string='Email')
    anagrafica_telefono = fields.Char(string='Phone')
    anagrafica_nome_legale_rappresentante = fields.Char(
        string='Legal representative — first name')
    anagrafica_cognome_legale_rappresentante = fields.Char(
        string='Legal representative — last name')
    anagrafica_cf_legale_rappresentante = fields.Char(
        string='Legal representative — tax code')
    anagrafica_qualita_legale_rappresentante = fields.Char(
        string='Legal representative — role')
    anagrafica_luogo_nascita_legale_rappresentante = fields.Char(
        string='Legal representative — place of birth')
    anagrafica_data_nascita_legale_rappresentante = fields.Date(
        string='Legal representative — date of birth')
    anagrafica_email_legale_rappresentante = fields.Char(
        string='Legal representative — email')

    # --- Local state -----------------------------------------------------------
    state = fields.Selection(
        [('proposta', 'Waiting for acceptance'),
         ('accettata', 'Accepted'),
         ('errore', 'Error')],
        string='Status', default='proposta', required=True, index=True)
    ciclo_completo = fields.Boolean(string='Cycle complete', readonly=True)
    data_accettazione = fields.Datetime(string='Accepted on', readonly=True)
    messaggio = fields.Text(string='Result', readonly=True)

    @api.depends('anagrafica_mancante')
    def _compute_richiede_anagrafica(self):
        for rec in self:
            rec.richiede_anagrafica = bool(rec.anagrafica_mancante)

    # ==========================================================================
    # Acceptance
    # ==========================================================================
    def _campi_mancanti(self):
        self.ensure_one()
        return [f.strip() for f in (self.anagrafica_mancante or '').split(',')
                if f.strip()]

    def _anagrafica_dict(self):
        """Build the `anagrafica` payload with the fields the user has filled
        in among those actually requested by the engine."""
        self.ensure_one()
        campi = self._campi_mancanti()
        if not campi:
            return {}
        valori = {}
        for nome in _ANAGRAFICA_FIELDS:
            valori[nome] = getattr(self, 'anagrafica_%s' % nome)
        data_nascita = valori.get('data_nascita_legale_rappresentante')
        if data_nascita:
            valori['data_nascita_legale_rappresentante'] = data_nascita.strftime('%Y-%m-%d')
        return {k: v for k, v in valori.items() if k in campi and v}

    def action_accetta(self):
        """Accept the selected compensation(s). Called both from the form
        (single record) and from the list header (possibly several rows)."""
        n_ok, n_err = 0, 0
        errori = []
        for rec in self:
            if rec.state == 'accettata':
                continue
            try:
                rec._accetta_singola()
                n_ok += 1
            except (CdcApiError, UserError) as exc:
                rec.write({'state': 'errore', 'messaggio': str(exc)})
                errori.append(_("#%s: %s") % (rec.id_compensazione, exc))
                n_err += 1

        if n_err and len(self) == 1:
            raise UserError('\n'.join(errori))

        tipo = 'success' if not n_err else ('warning' if n_ok else 'danger')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Acceptance"),
                'message': _("%(ok)s accepted, %(err)s in error.",
                             ok=n_ok, err=n_err),
                'type': tipo,
                'sticky': bool(n_err),
            },
        }

    def _accetta_singola(self):
        self.ensure_one()
        campi = self._campi_mancanti()
        anagrafica = self._anagrafica_dict()
        mancanti = [f for f in campi if f not in anagrafica]
        if mancanti:
            raise UserError(_(
                "Compensation #%(id)s: please fill in the following "
                "registry fields before accepting: %(fields)s",
                id=self.id_compensazione, fields=', '.join(mancanti)))

        client = self.config_id._client()
        client.connect()
        resp = client.accetta_compensazione(
            self.token, lingua=self.config_id._lingua(),
            anagrafica=anagrafica or None)

        ciclo_completo = bool(resp.get('ciclo_completo'))
        self.write({
            'state': 'accettata',
            'data_accettazione': fields.Datetime.now(),
            'ciclo_completo': ciclo_completo,
            'messaggio': (
                _("Accepted. The cycle is complete: the credit-assignment "
                  "letters will be issued shortly.") if ciclo_completo
                else _("Accepted. Waiting for the other participants of "
                       "the cycle to accept as well.")),
        })
