{
    # Max 25 caratteri richiesti dallo store (qui 22).
    'name': 'Nexyzen Invoice Offset',
    'version': '19.0.3.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Send unpaid invoices to Nexyzen to offset them without bank costs',
    'description': """
Nexyzen - Invoice Offsetting
============================

Nexyzen (the platform by Camera di Compensazione) offsets the receivables you
still have to collect against the invoices you have not paid yet, without bank
costs.

This module periodically sends to Nexyzen the unpaid customer invoices (active)
and vendor bills (passive), so that they can be offset.

Features
--------
* Setup screen with the selection criteria (days to due date, amount range,
  customers/vendors).
* Automatic sending or sending with manual approval of the list.
* Choice of the weekdays on which to send (Monday-Saturday).
* Unpaid invoices are re-proposed on every cycle until they are settled.
* Consult the compensations proposed by the clearing engine, complete the
  missing registry data of the assignor when requested, and accept them.
* Retrieve and print the credit-assignment letters issued once a
  compensation cycle is complete.

Multilingual: English, Italian, German, Slovenian, French, Spanish.
""",
    'author': 'Camera di Compensazione',
    'website': 'https://www.nexyzen.com',
    'support': 'commerciale@nexyzen.com',
    # App GRATUITA e open source (LGPL-3): nessun 'price'/'currency'.
    # Il ricavo deriva dal servizio di compensazione di Nexyzen, non dal plugin.
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'reports/cc_lettera_report.xml',
        'views/cc_config_views.xml',
        'views/cc_invio_views.xml',
        'views/cc_compensazione_views.xml',
        'views/cc_lettera_views.xml',
        'views/menu.xml',
    ],
    # La prima immagine è la cover/thumbnail mostrata nello store.
    'images': [
        'static/description/banner.png',
        'static/description/icon.png',
    ],
    'application': True,
    'installable': True,
}
