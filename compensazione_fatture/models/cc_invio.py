# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .cc_api import CdcApiError

_logger = logging.getLogger(__name__)


class CcInvio(models.Model):
    _name = 'cc.invio'
    _description = 'Invoice sent to Nexyzen'
    _order = 'create_date desc, id desc'
    _rec_name = 'move_id'

    config_id = fields.Many2one('cc.config', string='Configuration',
                                required=True, ondelete='cascade')
    move_id = fields.Many2one('account.move', string='Invoice',
                              required=True, ondelete='cascade')

    tipo = fields.Selection(
        [('v', 'Customer invoice (active)'), ('a', 'Vendor bill (passive)')],
        string='Type', compute='_compute_dati_move', store=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer/Vendor',
        compute='_compute_dati_move', store=True)
    numero_fattura = fields.Char(
        string='Number', compute='_compute_dati_move', store=True)
    data_scadenza = fields.Date(
        string='Due date', compute='_compute_dati_move', store=True)
    # Amount to send for offsetting: pre-filled with the unpaid residual but
    # EDITABLE by the user before sending (in manual-approval mode).
    importo_residuo = fields.Monetary(
        string='Amount to offset', store=True, readonly=False,
        currency_field='currency_id',
        help="Amount that will be sent for offsetting. Pre-filled with the "
             "unpaid residual; editable before sending.")
    currency_id = fields.Many2one(
        'res.currency', compute='_compute_dati_move', store=True)

    state = fields.Selection(
        [('da_inviare', 'To send'),
         ('inviata', 'Sent'),
         ('errore', 'Error')],
        string='Status', default='da_inviare', required=True, index=True)
    data_invio = fields.Datetime(string='Sent on', readonly=True)
    messaggio = fields.Text(string='Result', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        # Pre-fill the amount to offset with the move residual, if not provided.
        for vals in vals_list:
            if vals.get('move_id') and not vals.get('importo_residuo'):
                move = self.env['account.move'].browse(vals['move_id'])
                vals['importo_residuo'] = abs(move.amount_residual)
        return super().create(vals_list)

    @api.depends('move_id')
    def _compute_dati_move(self):
        for rec in self:
            move = rec.move_id
            rec.tipo = 'v' if move.move_type == 'out_invoice' else 'a'
            rec.partner_id = move.commercial_partner_id
            rec.numero_fattura = (
                move.ref if (move.move_type == 'in_invoice' and move.ref)
                else move.name)
            rec.data_scadenza = move.invoice_date_due or move.invoice_date
            rec.currency_id = move.currency_id

    # ------------------------------------------------------------------ sending
    def action_invia(self):
        """Send the selected rows to Nexyzen (grouped by configuration).

        One connection per configuration is opened and the JWT reused.
        """
        n_ok = 0
        n_err = 0
        for config, righe in self._group_by_config().items():
            try:
                client = config._client()
                client.connect()
            except CdcApiError as exc:
                # Connection failed: mark all rows as error.
                righe.write({
                    'state': 'errore',
                    'data_invio': fields.Datetime.now(),
                    'messaggio': _("Connection failed: %s") % exc,
                })
                n_err += len(righe)
                continue

            for riga in righe:
                try:
                    dati = config._dati_da_move(
                        riga.move_id, importo_residuo=riga.importo_residuo)
                    client.send_manual(dati)
                except (CdcApiError, UserError) as exc:
                    riga.write({
                        'state': 'errore',
                        'data_invio': fields.Datetime.now(),
                        'messaggio': str(exc),
                    })
                    n_err += 1
                else:
                    riga.write({
                        'state': 'inviata',
                        'data_invio': fields.Datetime.now(),
                        'messaggio': _("Sent successfully."),
                    })
                    n_ok += 1

        return self._notifica_esito(n_ok, n_err)

    def _group_by_config(self):
        gruppi = {}
        for riga in self:
            gruppi.setdefault(riga.config_id, self.env['cc.invio'])
            gruppi[riga.config_id] |= riga
        return gruppi

    def _notifica_esito(self, n_ok, n_err):
        if n_err and not n_ok:
            tipo, titolo = 'danger', _("Sending failed")
        elif n_err:
            tipo, titolo = 'warning', _("Partial sending")
        else:
            tipo, titolo = 'success', _("Sending completed")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': titolo,
                'message': _("%(ok)s sent, %(err)s in error.",
                             ok=n_ok, err=n_err),
                'type': tipo,
                'sticky': bool(n_err),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
