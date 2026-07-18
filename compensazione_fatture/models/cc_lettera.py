# -*- coding: utf-8 -*-
"""Credit-assignment letters ("lettere di cessione del credito") deposited by
Nexyzen once a compensation cycle is complete (swagger: /lettere_cessione).
"""
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class CcLettera(models.Model):
    _name = 'cc.lettera'
    _description = 'Credit-assignment letter received from Nexyzen'
    _order = 'lettera_id_cc desc'
    _rec_name = 'oggetto'

    config_id = fields.Many2one('cc.config', string='Configuration',
                                required=True, ondelete='cascade')

    lettera_id_cc = fields.Integer(
        string='Letter ID', required=True, index=True,
        help="Identifier of the letter on the Nexyzen side (field `id`).")
    id_compensazione = fields.Integer(string='Compensation ID')
    oggetto = fields.Char(string='Subject')
    corpo_html = fields.Html(
        string='Body', sanitize=False, readonly=True,
        help="Full text of the letter, as delivered by Nexyzen.")
    data_creazione = fields.Char(string='Issued on', readonly=True)
    data_consegna = fields.Char(
        string='Retrieved on', readonly=True,
        help="Date the letter was first retrieved through this channel.")

    def action_stampa_pdf(self):
        self.ensure_one()
        return self.env.ref(
            'compensazione_fatture.action_report_cc_lettera'
        ).report_action(self)
