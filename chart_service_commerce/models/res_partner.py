# -*- coding: utf-8 -*-
"""Invalidación de la verificación de email al cambiar el correo del partner.

La verificación de email de un contrato queda ligada al destinatario que la
confirmó (email_verified_recipient). Si el correo del partner cambia, la
verificación anterior deja de ser válida: se invalida para que el cliente
verifique la nueva dirección antes de continuar.
"""
from odoo import models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def write(self, vals):
        res = super().write(vals)
        if 'email' in vals:
            contracts = self.env['chart.service.contract'].search([
                ('partner_id', 'in', self.ids),
                ('email_verified', '=', True),
            ])
            contracts._invalidate_email_verification()
        return res
