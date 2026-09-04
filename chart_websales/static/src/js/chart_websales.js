/* ============================================================
   Grupo Chart — Websales
   Interactividad ligera (sin tocar el JS nativo de precios de Odoo):
   1. Animaciones on-scroll (Intersection Observer).
   2. Highlight del radio de variante seleccionado.
   El cambio de precio al seleccionar variantes ya lo hace Odoo.
   ============================================================ */
odoo.define('chart_websales.chart_websales', function (require) {
    'use strict';

    const { onMounted, onWillUnmount } = require('@odoo/owl');
    const { patch } = require('web.utils');
    const publicWidget = require('web.public.widget');

    const ChartWebsales = publicWidget.Widget.extend({
        selector: '#wrap',
        events: {
            'change .o_wsale_product_page .js_variant_change': '_onVariantChange',
        },

        start() {
            this._setupRevealObserver();
            return this._super.apply(this, arguments);
        },

        _setupRevealObserver() {
            if (!('IntersectionObserver' in window)) {
                return;
            }
            const elements = this.el.querySelectorAll('.chart-showcase, .chart-testimonials');
            const observer = new IntersectionObserver((entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting) {
                        entry.target.classList.add('chart-in');
                        observer.unobserve(entry.target);
                    }
                });
            }, { threshold: 0.12 });
            elements.forEach((el) => {
                el.classList.add('chart-reveal');
                observer.observe(el);
            });
            this._revealObserver = observer;
        },

        _onVariantChange(ev) {
            const input = ev.target;
            if (!input || input.type !== 'radio') {
                return;
            }
            // Destaca la tarjeta de la variante seleccionada (radio buttons)
            const li = input.closest('li.js_attribute_value');
            if (li) {
                const siblings = li.parentElement.querySelectorAll('li.js_attribute_value');
                siblings.forEach((s) => s.classList.remove('chart-variant-picked'));
                li.classList.add('chart-variant-picked');
            }
        },

        destroy() {
            if (this._revealObserver) {
                this._revealObserver.disconnect();
            }
            this._isDestroyed = true;
            return this._super.apply(this, arguments);
        },
    });

    return publicWidget;
});

// Cambio dinámico de precio: Odoo lo resuelve nativamente vía
// combination_info + JS de website_sale (product_variants). No lo
// duplicamos para no romper el flujo oficial del carrito.