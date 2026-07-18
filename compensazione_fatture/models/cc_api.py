# -*- coding: utf-8 -*-
"""HTTP client for the Nexyzen (Camera di Compensazione) webservice.

OpenAPI documentation: https://cameracompensazione.github.io/cc_webservice/
Flow: /connect (op=gjwt) to obtain a JWT, then /send_manual (op=ins_manuale)
to send the essential data of an invoice.
"""
import logging

import requests

_logger = logging.getLogger(__name__)

# Timeout (secondi) per le chiamate HTTP verso il webservice.
_TIMEOUT = 30


class CdcApiError(Exception):
    """Error returned by the webservice or network problem."""


class CdcClient:
    """Small stateful client: opens the connection once and reuses the JWT."""

    def __init__(self, base_url, cod_affiliato, token):
        # base_url must point to .../webservices/index.php
        self.base_url = (base_url or '').strip()
        self.cod_affiliato = (cod_affiliato or '').strip()
        self.token = (token or '').strip()
        self._jwt = None

    # ------------------------------------------------------------------ internal
    def _post(self, payload):
        try:
            resp = requests.post(
                self.base_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise CdcApiError("Unable to contact the webservice: %s" % exc)

        try:
            body = resp.json()
        except ValueError:
            raise CdcApiError(
                "Non-JSON response (HTTP %s): %s" % (resp.status_code, resp.text[:200])
            )

        if resp.status_code >= 400:
            msg = body.get('message') if isinstance(body, dict) else None
            raise CdcApiError(msg or ("HTTP error %s" % resp.status_code))

        return body

    # ------------------------------------------------------------------ public
    def connect(self):
        """Obtain (and store) a valid JWT. Returns the JWT token."""
        body = self._post({
            'op': 'gjwt',
            'dati': {
                'cod_affiliato': self.cod_affiliato,
                'token': self.token,
            },
        })
        jwt = body.get('jwt') if isinstance(body, dict) else None
        if not jwt:
            raise CdcApiError("Login succeeded but JWT missing in response")
        self._jwt = jwt
        return jwt

    def send_manual(self, dati):
        """Send the essential data of an invoice (op=ins_manuale).

        `dati` is a dict with the keys expected by the swagger. Returns the
        response body on success, otherwise raises CdcApiError.
        """
        if not self._jwt:
            self.connect()
        body = self._post({
            'op': 'ins_manuale',
            'jwt': self._jwt,
            'dati': dati,
        })
        # The webservice replies {"result": "ok"} on success.
        if isinstance(body, dict) and body.get('result') not in ('ok', 'Successful login'):
            # result other than ok: treat the message as an error.
            raise CdcApiError(body.get('message') or "Unexpected result: %s" % body)
        return body

    def _post_op(self, op, dati):
        """Shared helper for the op=X/jwt/dati calls added for the
        compensations channel (get_compensazioni, accetta_compensazione,
        get_lettere_cessione). Raises CdcApiError unless result == 'ok'.
        """
        if not self._jwt:
            self.connect()
        body = self._post({'op': op, 'jwt': self._jwt, 'dati': dati})
        if isinstance(body, dict) and body.get('result') != 'ok':
            raise CdcApiError(body.get('message') or "Unexpected result: %s" % body)
        return body

    def get_compensazioni(self, partita_iva, lingua='it'):
        """Fetch the compensations proposed by the clearing engine and still
        waiting for acceptance, for the given VAT (op=get_compensazioni)."""
        return self._post_op('get_compensazioni', {
            'partita_iva': partita_iva,
            'lingua': lingua,
        })

    def accetta_compensazione(self, token, lingua='it', anagrafica=None):
        """Accept a proposed compensation by its one-time token
        (op=accetta_compensazione), optionally completing the missing
        registry data of the assignor (cedente)."""
        dati = {'token': token, 'lingua': lingua}
        if anagrafica:
            dati['anagrafica'] = anagrafica
        return self._post_op('accetta_compensazione', dati)

    def get_lettere_cessione(self, partita_iva, lingua='it', tutte=False):
        """Fetch the credit-assignment letters deposited for the given VAT
        (op=get_lettere_cessione). tutte=True also returns letters already
        retrieved in a previous call."""
        return self._post_op('get_lettere_cessione', {
            'partita_iva': partita_iva,
            'lingua': lingua,
            'tutte': bool(tutte),
        })
