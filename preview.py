"""Genera vistas previas de los tickets con datos de ejemplo, sin impresora ni base de datos.

Uso: python preview.py [boleta|factura|nd|turno|todos]
Los archivos quedan en la carpeta preview/ y se abren en el navegador.
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace as NS

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
os.environ['PREVIEW'] = '1'
os.environ.setdefault('USB_VENDOR_ID', '0')
os.environ.setdefault('USB_PRODUCT_ID', '0')
os.environ.setdefault('RS', 'ESTACION DE SERVICIO DEMO')
os.environ.setdefault('RUC', '20123456789')

import index
import impresora_preview

AHORA = datetime.now().isoformat()
USUARIO = NS(nombre='JUAN PEREZ')
ITEMS = [
    NS(descripcion='GASOHOL 90', cantidad=10.5, precio=17.50, valor_venta=183.75),
    NS(descripcion='ACEITE MOTOR 1L', cantidad=1, precio=35.00, valor_venta=35.00),
]


def comprobante(**kw):
    datos = dict(
        fecha_hora=AHORA, tipo_comprobante='BOLETA ELECTRONICA', numeracion_comprobante='B001-00012345',
        isla_id=1, pistola_num=2, placa='ABC-123', gravadas=185.17, igv=33.33, total=218.50,
        codigo_tipo='03', cadena_para_codigo_qr='20123456789|03|B001|00012345|33.33|218.50',
        codigo_hash='JIiuNpvquEH36J7wZ0f8emtHWNE=',
    )
    datos.update(kw)
    return NS(**datos)


def boleta():
    receptor = NS(razon_social='CLIENTES VARIOS', tipo_documento='DNI', numero_documento='00000000', direccion='')
    index.imprimir_comprobante(comprobante(), ITEMS, receptor, USUARIO)


def factura():
    receptor = NS(razon_social='TRANSPORTES EL RAPIDO S.A.C.', tipo_documento='RUC',
                  numero_documento='20456789012', direccion='AV. LOS OLIVOS 456, LIMA')
    index.imprimir_comprobante(
        comprobante(tipo_comprobante='FACTURA ELECTRONICA', numeracion_comprobante='F001-00000678', codigo_tipo='01'),
        ITEMS, receptor, USUARIO)


def nota_despacho():
    receptor = NS(razon_social='TRANSPORTES EL RAPIDO S.A.C.', tipo_documento='RUC',
                  numero_documento='20456789012', direccion='AV. LOS OLIVOS 456, LIMA')
    index.imprimir_comprobante(
        comprobante(tipo_comprobante='NOTA DE DESPACHO', numeracion_comprobante='ND01-00000042', codigo_tipo='50',
                    cadena_para_codigo_qr=None, codigo_hash=None),
        ITEMS, receptor, USUARIO)


def turno():
    cierre = NS(fecha=AHORA, fecha_inicio='2026-10-31T06:00:00', turno='MAÑANA', isla=1,
                efectivo=1520.50, tarjeta=430.00, yape=210.00, total=2160.50)
    detalles = [
        NS(producto='GASOHOL 90', total_cantidad=85.2, total_soles=1491.00, calibracion_cantidad=0.5, despacho_cantidad=10),
        NS(producto='DIESEL B5', total_cantidad=40.0, total_soles=669.50, calibracion_cantidad=0, despacho_cantidad=0),
    ]
    despachos = [NS(razon_social='TRANSPORTES EL RAPIDO', nombre='DIESEL B5', volumen=10.0, total=167.50)]
    depositos = [NS(concepto='DEPOSITO BANCO', fecha='2026-10-31T10:15:00', monto=800.00)]
    gastos = [NS(concepto='LIMPIEZA', fecha='2026-10-31T11:30:00', monto=25.00)]
    index.imprimir_ticket_cierre_turno(cierre, USUARIO, detalles, depositos, gastos, despachos)


TICKETS = {'boleta': boleta, 'factura': factura, 'nd': nota_despacho, 'turno': turno}

if __name__ == '__main__':
    eleccion = sys.argv[1] if len(sys.argv) > 1 else 'todos'
    for nombre, funcion in TICKETS.items():
        if eleccion in ('todos', nombre):
            funcion()
    for ruta in impresora_preview.archivos_generados:
        os.startfile(ruta)
