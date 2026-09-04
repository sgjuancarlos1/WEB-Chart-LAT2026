# Part of Grupo Chart. See LICENSE file for full copyright and licensing details.
"""chart_websales — Websales & Conversión E-commerce.

Este módulo NUNCA edita vistas del core de Odoo, del tema Cobalt ni de
chart_website. Todo = herencia (inherit_id + xpath) o registros propios.
"""

from . import models

# post_init_hook usado por __manifest__.py
from .models import product_template
post_init_hook = product_template.post_init_hook