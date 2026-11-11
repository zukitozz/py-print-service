import base64
import html
import io
import os
from datetime import datetime

from escpos.printer import Dummy

CARPETA_PREVIEW = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preview')
COLUMNAS = 48  # Caracteres por linea en la TM-T88V (papel 80 mm, fuente A)

archivos_generados = []


class PreviewPrinter(Dummy):
    """Impresora falsa: no necesita hardware. Al hacer cut() guarda el ticket
    como .html (vista previa) y .bin (bytes ESC/POS exactos que iria a la Epson)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = None  # el codigo de impresion hace `if p.device:`
        self._estilo = {'align': 'left', 'bold': False, 'width': 1, 'height': 1}
        self._buffer = ''
        self._items = []

    def set(self, align=None, bold=None, width=None, height=None, **kwargs):
        for clave, valor in (('align', align), ('bold', bold), ('width', width), ('height', height)):
            if valor is not None:
                self._estilo[clave] = valor
        super().set(align=align, bold=bold, width=width, height=height, **kwargs)

    def text(self, txt):
        self._buffer += txt
        while '\n' in self._buffer:
            linea, self._buffer = self._buffer.split('\n', 1)
            self._items.append(('texto', linea, dict(self._estilo)))
        super().text(txt)

    def image(self, img_source, *args, **kwargs):
        self._items.append(('imagen', img_source, dict(self._estilo)))
        super().image(img_source, *args, **kwargs)

    def cut(self, *args, **kwargs):
        super().cut(*args, **kwargs)
        self._items.append(('corte', '', {}))
        self._guardar()

    def _guardar(self):
        os.makedirs(CARPETA_PREVIEW, exist_ok=True)
        base = os.path.join(CARPETA_PREVIEW, datetime.now().strftime('ticket_%Y%m%d_%H%M%S_%f'))
        with open(base + '.html', 'w', encoding='utf-8') as f:
            f.write(self._html())
        with open(base + '.bin', 'wb') as f:
            f.write(self.output)
        archivos_generados.append(base + '.html')
        print(f"Preview guardado: {base}.html")

    def _html(self):
        cuerpo = []
        for tipo, contenido, estilo in self._items:
            if tipo == 'corte':
                cuerpo.append('<div class="corte">&#9988; - - - - - - - - - - - - - - -</div>')
            elif tipo == 'imagen':
                cuerpo.append(f'<div style="text-align:{estilo["align"]};line-height:0">{_imagen_html(contenido)}</div>')
            else:
                cuerpo.append(_linea_html(contenido, estilo))
        return (
            '<!doctype html><html><head><meta charset="utf-8"><title>Preview ticket</title>'
            '<style>'
            'body{background:#d9d9d9;margin:0;padding:24px;display:flex;justify-content:center}'
            f'.ticket{{background:#fff;width:{COLUMNAS}ch;padding:16px;box-shadow:0 2px 8px #0005;'
            'font-family:"Courier New",monospace;font-size:14px;line-height:1.25;color:#000}'
            '.l{white-space:pre-wrap;word-break:break-all;min-height:1.25em}'
            '.corte{text-align:center;color:#888;margin-top:16px;border-top:1px dashed #888;padding-top:4px}'
            '</style></head><body><div class="ticket">'
            + ''.join(cuerpo) +
            '</div></body></html>'
        )


def _linea_html(texto, estilo):
    ancho, alto = estilo['width'], estilo['height']
    css = f'text-align:{estilo["align"]};'
    if estilo['bold']:
        css += 'font-weight:bold;'
    if ancho == alto and ancho > 1:
        css += f'zoom:{ancho};'
    elif alto > 1:
        css += f'transform:scaleY({alto});transform-origin:top;margin-bottom:{(alto - 1) * 1.25}em;'
    return f'<div class="l" style="{css}">{html.escape(texto) or "&nbsp;"}</div>'


def _imagen_html(img):
    # La imagen se imprime a 1 punto por pixel (576 puntos = COLUMNAS caracteres)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    datos = base64.b64encode(buf.getvalue()).decode()
    ancho = img.width / 576 * COLUMNAS
    return f'<img src="data:image/png;base64,{datos}" style="width:{ancho}ch" alt="imagen">'
