from datetime import datetime, timezone, timedelta

import pyodbc
import time
import os
import threading
from dotenv import load_dotenv
from escpos.printer import Usb

# Cargar configuración
base_path = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(base_path, '.env'))

lock_impresora = threading.Lock()

EMP_CONFIG = {
    'rs': os.getenv('RS'),
    'ruc': os.getenv('RUC'),
    'ubigeo': os.getenv('UBIGEO'),
    'direccion': os.getenv('EMISOR_DIR'),
    'nc': ''
}
# --- CONFIGURACIÓN DE IMPRESORA (EPSON TM-T88V) ---
# Verifica estos IDs en el administrador de dispositivos
# Convertir IDs de hex string a entero
ID_VENDOR = int(os.getenv('USB_VENDOR_ID'), 16)
ID_PRODUCT = int(os.getenv('USB_PRODUCT_ID'), 16)

# --- CONFIGURACIÓN ---
INTERVALO_SUNAT = int(os.getenv('INTERVALO_SUNAT', 60))    # Cada 1 min
INTERVALO_IMPRESION = int(os.getenv('INTERVALO_IMPRESION', 30)) # Cada 30 seg
MAX_SIZE = int(os.getenv('MAX_SIZE', 40)) # Ancho máximo para impresión
ISLAS = os.getenv('ISLAS')
ISLA_CIERRE = os.getenv('ISLA_CIERRE')
# --- CONFIGURACIÓN MIFACT ---
MIFACT_API_URL = os.getenv('MIFACT_API')
MIFACT_TOKEN = os.getenv('MIFACT_TOKEN')

def obtener_conexion():
    return pyodbc.connect(
        f"DRIVER={os.getenv('DB_DRIVER')};SERVER={os.getenv('DB_SERVER')};"
        f"DATABASE={os.getenv('DB_DATABASE')};UID={os.getenv('DB_USER')};PWD={os.getenv('DB_PASSWORD')}"
    )

# --- PROCESO 2: IMPRESIÓN FÍSICA ---
def proceso_impresion():
    print(f"🖨️ Hilo de IMPRESIÓN iniciado (Cada {INTERVALO_IMPRESION}s)")
    while True:
        conn = None
        try:
            conn = obtener_conexion()
            cursor = conn.cursor()
            
            # Buscar comprobantes NO impresos (impreso = 0)

            query = f"""
                    SELECT id, CASE tipo_comprobante WHEN '01' THEN 'FACTURA ELECTRONICA' WHEN '03' THEN 'BOLETA ELECTRONICA' WHEN '07' THEN 'NOTA DE CREDITO ELECTRONICA' WHEN '08' THEN 'NOTA DE DEBITO ELECTRONICA' WHEN '50' THEN 'NOTA DE DESPACHO' WHEN '51' THEN 'CALIBRACION' WHEN '52' THEN 'NOTA INTERNA' END as tipo_comprobante, numeracion_comprobante, placa, fecha_emision, gravadas, total, igv, cadena_para_codigo_qr, codigo_hash, ReceptorId, UsuarioId 
                    FROM Comprobantes  c 
                    WHERE (impresion = 0 or impresion is null) AND IslaID IN ({ISLAS}) order by id desc"""
            cursor.execute(query)
            pendientes = cursor.fetchall()

            for comp in pendientes:
                with lock_impresora:
                    print(f"📄 Imprimiendo: {comp.numeracion_comprobante}")
                    
                    cursor.execute("SELECT CASE WHEN tipo_documento = '0' THEN 'DNI' WHEN tipo_documento = '1' THEN 'CARNET DE EXTRANJERIA' WHEN tipo_documento = '4' THEN 'CARNET DE IDENTIDAD' WHEN tipo_documento = '6' THEN 'RUC' ELSE 'OTRO' END as tipo_documento, numero_documento, razon_social, direccion, correo FROM Receptores WHERE id = ?", comp.ReceptorId)
                    receptor = cursor.fetchone()
                    cursor.execute("SELECT * FROM Items WHERE ComprobanteId = ?", comp.id)
                    items = cursor.fetchall()
                    cursor.execute("SELECT * FROM Usuarios WHERE id = ?", comp.UsuarioId)
                    usuario = cursor.fetchone()

                    # La función imprimir_comprobante usará comp.cadena_para_codigo_qr 
                    # si ya existe (porque el hilo 1 terminó) o el QR por defecto (si no ha terminado)
                    if imprimir_comprobante(comp, items, receptor, usuario):
                        cursor.execute("UPDATE Comprobantes SET impresion = 1 WHERE id = ?", comp.id)
                        conn.commit()
                        print(f"🖨️ {comp.numeracion_comprobante} marcado como IMPRESO.")

        except Exception as e:
            print(f"❌ Error en hilo Impresión: {e}")
        finally:
            if conn: conn.close()
            
        time.sleep(INTERVALO_IMPRESION)

# --- PROCESO 3: IMPRESIÓN DE CIERRES ---
def proceso_cierres():
    print(f"📊 Hilo de CIERRES iniciado (Cada 60s)")
    while True:
        conn = None
        try:
            conn = obtener_conexion()
            cursor = conn.cursor()
            
            # 1. Buscar Cierres de Turno pendientes
            # Nota: Asumo que agregaste una columna 'impresion' a Cierreturnos
            query_turnos = """
                SELECT id, total, fecha, fecha_inicio, turno, isla, efectivo, tarjeta, yape, CierrediaId, UsuarioId 
                FROM Cierreturnos 
                WHERE (impresion = 0 OR impresion IS NULL) and isla = ?
            """
            cursor.execute(query_turnos, ISLA_CIERRE)
            turnos_pendientes = cursor.fetchall()

            for turno in turnos_pendientes:
                # Obtener el detalle del turno
                with lock_impresora:
                    cursor.execute("SELECT * FROM Cierreturnosdetalle WHERE CierreturnoId = ?", turno.id)
                    detalles = cursor.fetchall()

                    cursor.execute("SELECT * FROM Usuarios WHERE id = ?", turno.UsuarioId)
                    usuario = cursor.fetchone()

                    cursor.execute("SELECT * FROM Depositos WHERE CierreturnoId = ?", turno.id)
                    depositos = cursor.fetchall()

                    cursor.execute("SELECT * FROM Gastos WHERE CierreturnoId = ?", turno.id)
                    gastos = cursor.fetchall()

                    cursor.execute("SELECT r.razon_social, p.nombre, c.volumen, c.total FROM Comprobantes c INNER JOIN Receptores r ON c.ReceptorId = r.id INNER JOIN Productos p ON c.codigo_combustible = p.id WHERE c.tipo_comprobante = '50' and CierreturnoId = ?", turno.id)
                    despachos = cursor.fetchall()
                    
                    print(f"🖨️ Imprimiendo Cierre Turno: {turno.turno} - Isla: {turno.isla}")
                    if imprimir_ticket_cierre_turno(turno, usuario, detalles, depositos, gastos, despachos):
                        cursor.execute("UPDATE Cierreturnos SET impresion = 1 WHERE id = ?", turno.id)
                        conn.commit()

            # 2. Opcional: Buscar Cierres de Día (si estado es 'CERRADO' e impresion = 0)
            cursor.execute("SELECT * FROM Cierredias WHERE impresion = 0 OR impresion IS NULL and isla = ?", ISLA_CIERRE)
            dias_pendientes = cursor.fetchall()
            for dia in dias_pendientes:
                with lock_impresora:
                    if imprimir_ticket_cierre_dia(dia):
                        cursor.execute("UPDATE Cierredias SET impresion = 1 WHERE id = ?", dia.id)
                        conn.commit()

        except Exception as e:
            print(f"❌ Error en hilo Cierres: {e}")
        finally:
            if conn: conn.close()
        
        time.sleep(INTERVALO_IMPRESION) # Revisar cierres cada minuto        

def imprimir_comprobante(comprobante, items, receptor, usuario):
    p = None
    try:
        p = Usb(ID_VENDOR, ID_PRODUCT, profile="TM-T88V")        
        # Encabezado de la Gasolinera
        p.set(align='center', bold=True, width=2, height=2)
        p.text(f"{EMP_CONFIG['rs']}\n")
        p.text(f"{EMP_CONFIG['ruc']}\n")
        p.text(f"{comprobante.tipo_comprobante}\n") # Factura o Boleta
        p.set(align='center', bold=False, width=1, height=1)
        p.text(f"{comprobante.numeracion_comprobante}\n")
        p.text("-" * MAX_SIZE + "\n")

        # Datos del Cliente
        p.set(align='left')
        p.text(f"CLIENTE: {receptor.razon_social[:30]}\n")
        p.text(f"{receptor.tipo_documento}: {receptor.numero_documento}\n")
        if receptor.tipo_documento == "RUC":
            p.text(f"DIRECCION: {receptor.direccion[:30]}\n")  
        if comprobante.placa:
            p.text(f"PLACA: {comprobante.placa}\n")
        p.text(f"FECHA: {comprobante.fecha_emision}\n")
        p.text(f"USUARIO: {usuario.nombre[:30]}\n")
        p.text("-" * MAX_SIZE + "\n")

        # Detalle de Items
        p.text("CANT  DESCRIPCION         PRECIO   TOTAL\n")
        for item in items:
            desc = item.descripcion[:18].ljust(18)
            cant = str(item.cantidad).ljust(5)
            prec = f"{item.precio:>7.2f}"
            tot = f"{item.valor_venta:>7.2f}"
            p.text(f"{cant} {desc} {prec} {tot}\n")

        # Totales
        p.text("-" * MAX_SIZE + "\n")
        p.set(align='right', bold=True)
        p.text(f"OP. GRAVADA: S/ {comprobante.gravadas:.2f}\n")
        p.text(f"IGV (18%):   S/ {comprobante.igv:.2f}\n")
        p.text(f"TOTAL A PAGAR: S/ {comprobante.total:.2f}\n\n")

        # Código QR y Hash
        p.set(align='center')
        if comprobante.cadena_para_codigo_qr:
            p.qr(comprobante.cadena_para_codigo_qr, size=6)
        else:
            p.qr(f"{EMP_CONFIG['ruc']}|{comprobante.tipo_comprobante}|{comprobante.numeracion_comprobante}", size=6)

        if comprobante.codigo_hash:
            p.text(f"Hash: {comprobante.codigo_hash[:20]}...\n")
        else:
            p.text("Hash: JIiuNpvquEH36J7wZ0f8emtHWNE=...\n")
        
        p.cut()

        if p.device:
            from usb.util import dispose_resources
            dispose_resources(p.device)        
        return True
    except Exception as e:
        print(f"Error físico de impresión: {e}")
        return False
    finally:
        if p: del p
        time.sleep(1)

def imprimir_ticket_cierre_turno(cierre, usuario, detalles, depositos, gastos, despachos):
    # , "%Y-%m-%d %H:%M:%S"
    p = None
    dt_obj = datetime.fromisoformat(cierre.fecha)  
    fecha_cierre = dt_obj.strftime("%d %B %Y %H:%M:%S")
    dt_obj_inicio = datetime.fromisoformat(cierre.fecha_inicio) 
    fecha_inicio = dt_obj_inicio.strftime("%d %B %Y %H:%M:%S")
    try:
        sum_depositos = sum(float(d.monto) for d in depositos)
        sum_gastos = sum(float(g.monto) for g in gastos)
        sum_gastos = sum(float(g.monto) for g in gastos)
        total_turno = float(cierre.total) - sum_depositos - sum_gastos
        p = Usb(ID_VENDOR, ID_PRODUCT, profile="TM-T88V")
        p.set(align='center', bold=True)
        p.text("REPORTE DE CIERRE DE TURNO\n")
        p.text(f"{EMP_CONFIG['rs']}\n")
        p.text("-" * MAX_SIZE + "\n")
        
        p.set(align='left', bold=False)
        
        p.text(f"TURNO: {cierre.turno}   ISLA: {cierre.isla}\n")
        p.text(f"USUARIO: {usuario.nombre}\n")        
        p.text(f"INICIO: {fecha_inicio}\n")
        p.text(f"FIN: {fecha_cierre}\n")
        p.text("-" * MAX_SIZE + "\n")
        
        # Encabezados de detalle
        p.set(align='center', bold=True)
        p.text("CIERRE DE PRODUCTOS\n")
        p.set(align='left', bold=False)
        p.text("PRODUCTO          CANTIDAD      SOLES\n")
        for d in detalles:
            prod = d.producto[:15].ljust(16)
            cant = f"{float(d.total_cantidad):>9.2f}"
            soles = f"{float(d.total_soles):>9.2f}"
            p.text(f"{prod} {cant} {soles}\n")
            # Si hay calibración, mostrarla debajo
            if float(d.calibracion_cantidad) > 0:
                p.text(f"  (Calib: -{d.calibracion_cantidad})\n")
                p.text(f"  (Despa: -{d.despacho_cantidad})\n")

        p.text("-" * MAX_SIZE + "\n")
        # Encabezados de notas de despacho
        p.set(align='center', bold=True)
        p.text("DETALLE NOTAS DE DESPACHO\n")
        p.set(align='left', bold=False)
        p.text("CLIENTE     PRODUCTO      CANT.    SOLES\n")
        for de in despachos:
            cant = f"{float(de.volumen):>9.2f}"
            soles = f"{float(de.total):>9.2f}"
            cliente = de.razon_social[:11].ljust(11)
            prod = de.nombre[:8].ljust(8)            
            p.text(f"{cliente} {prod} {cant} {soles}\n")

        # --- SECCIÓN DE DEPÓSITOS ACTUALIZADA ---
        p.text("-" * MAX_SIZE + "\n")
        p.set(align='center', bold=True)
        p.text("DEPOSITOS\n")
        p.set(align='left', bold=False)
        p.text("CONCEPTO         FECHA/HORA     SOLES\n") # Encabezado reestructurado (40 caracteres)
        for d in depositos:
            # Intentar formatear la fecha del depósito (asumiendo que viene como string ISO o datetime de BD)
            try:
                dt_dep = datetime.fromisoformat(str(d.fecha))
                f_dep = dt_dep.strftime("%d/%m %H:%M") # Formato corto "20/05 21:15"
            except Exception:
                f_dep = str(d.fecha)[:11] # Fallback si no se puede parsear
                
            concepto = d.concepto[:15].ljust(16)
            fecha_str = f_dep.ljust(12)
            monto = f"{float(d.monto):>9.2f}"
            p.text(f"{concepto} {fecha_str} {monto}\n")

        # --- SECCIÓN DE GASTOS ACTUALIZADA ---
        p.text("-" * MAX_SIZE + "\n")
        p.set(align='center', bold=True)
        p.text("GASTOS\n")
        p.set(align='left', bold=False)
        p.text("CONCEPTO         FECHA/HORA     SOLES\n") # Encabezado reestructurado (40 caracteres)
        for g in gastos:
            # Intentar formatear la fecha del gasto
            try:
                dt_gas = datetime.fromisoformat(str(g.fecha))
                f_gas = dt_gas.strftime("%d/%m %H:%M")
            except Exception:
                f_gas = str(g.fecha)[:11]
                
            concepto = g.concepto[:15].ljust(16)
            fecha_str = f_gas.ljust(12)
            monto = f"{float(g.monto):>9.2f}"
            p.text(f"{concepto} {fecha_str} {monto}\n")            

        p.text("-" * MAX_SIZE + "\n")
        p.set(align='right')
        p.text(f"EFECTIVO: S/ {cierre.efectivo:>9.2f}\n")
        p.text(f"TARJETA:  S/ {cierre.tarjeta:>9.2f}\n")
        p.text(f"YAPE/PLIN: S/ {cierre.yape:>9.2f}\n")
        p.set(bold=True)
        p.text(f"SUB TOTAL TURNO: S/ {cierre.total:>9.2f}\n")
        p.text(f"DEPOSITOS: S/ {sum_depositos:>9.2f}\n")
        p.text(f"GASTOS: S/ {sum_gastos:>9.2f}\n")
        p.text(f"*TOTAL TURNO: S/ {total_turno:>9.2f}\n")
        p.text("-" * MAX_SIZE + "\n")
        p.text("* TOTAL TURNO = Total Bruto - Depósitos - Gastos - Notas de Despacho - Calibración\n")

        p.cut()

        if p.device:
            from usb.util import dispose_resources
            dispose_resources(p.device)        
        return True
    except Exception as e:
        print(f"Error impresion turno: {e}")
        return False
    finally:
        if p: del p
        time.sleep(1)

def imprimir_ticket_cierre_dia(dia):
    p = None
    try:
        # 1. Obtener conexión
        conn = obtener_conexion()
        cursor = conn.cursor()
        
        # --- CONSULTA 1: Resumen de Ventas por Producto (Consolidado) ---
        query_productos = """
            SELECT ctd.producto, SUM(ctd.total_cantidad) as cant, SUM(ctd.total_soles) as soles
            FROM Cierreturnosdetalle ctd
            INNER JOIN Cierreturnos ct ON ctd.CierreturnoId = ct.id
            WHERE ct.CierrediaId = ?
            GROUP BY ctd.producto
        """
        cursor.execute(query_productos, dia.id)
        productos_dia = cursor.fetchall()

        # --- CONSULTA 2: Resúmenes Finales por cada Cierre de Turno ---
        query_resumen_turnos = """
            SELECT ct.turno, ct.isla, u.nombre as operador, ct.total
            FROM Cierreturnos ct
            LEFT JOIN Usuarios u ON ct.UsuarioId = u.id
            WHERE ct.CierrediaId = ?
            ORDER BY ct.turno ASC, ct.isla ASC
        """
        cursor.execute(query_resumen_turnos, dia.id)
        turnos_dia = cursor.fetchall()

        # --- CONSULTA 3: NUEVO - Depósitos Consolidados del Día ---
        query_depositos = """
            SELECT d.concepto, SUM(d.monto) as total_monto
            FROM Depositos d
            INNER JOIN Cierreturnos ct ON d.CierreturnoId = ct.id
            WHERE ct.CierrediaId = ?
            GROUP BY d.concepto
        """
        cursor.execute(query_depositos, dia.id)
        depositos_dia = cursor.fetchall()

        # --- CONSULTA 4: NUEVO - Gastos Consolidados del Día ---
        query_gastos = """
            SELECT g.concepto, SUM(g.monto) as total_monto
            FROM Gastos g
            INNER JOIN Cierreturnos ct ON g.CierreturnoId = ct.id
            WHERE ct.CierrediaId = ?
            GROUP BY g.concepto
        """
        cursor.execute(query_gastos, dia.id)
        gastos_dia = cursor.fetchall()

        # --- CONSULTA 5: Consolidado de Medios de Pago ---
        query_pagos = """
            SELECT 
                SUM(ISNULL(efectivo, 0)) as total_efectivo,
                SUM(ISNULL(tarjeta, 0)) as total_tarjeta,
                SUM(ISNULL(yape, 0)) as total_yape
            FROM Cierreturnos
            WHERE CierrediaId = ?
        """
        cursor.execute(query_pagos, dia.id)
        pagos = cursor.fetchone()
        
        # Formatear la fecha para el ticket
        try:
            dt_obj = datetime.fromisoformat(str(dia.fecha))
            fecha_formateada = dt_obj.strftime("%d/%m/%Y %H:%M")
        except Exception:
            fecha_formateada = str(dia.fecha)

        # 2. Inicializar Impresora Epson
        p = Usb(ID_VENDOR, ID_PRODUCT, profile="TM-T88V")
        
        # --- ENCABEZADO CORPORATIVO ---
        p.set(align='center', bold=True)
        p.text(f"{EMP_CONFIG['rs']}\n")
        p.text(f"RUC: {EMP_CONFIG['ruc']}\n")
        p.text("-" * MAX_SIZE + "\n")
        
        p.set(align='center', bold=True, width=1, height=2)
        p.text("X - CIERRE CONSOLIDADO DIARIO\n")
        
        p.set(align='left', bold=False, width=1, height=1)
        p.text(f"FECHA EMISION : {fecha_formateada}\n")
        p.text(f"CIERRE DIA ID : {dia.id}\n")
        p.text("-" * MAX_SIZE + "\n")
        
        # --- SECCIÓN 1: RESUMEN DE VENTAS GENERALES ---
        p.set(align='center', bold=True)
        p.text("[ RESUMEN DE PRODUCTOS ]\n\n")
        p.set(align='left', bold=False)
        p.text("PRODUCTO         CANTIDAD       SOLES\n")
        p.text("----------------------------------------\n")
        for prod in productos_dia:
            nom_prod = prod.producto[:15].ljust(16)
            cantidad = f"{float(prod.cant):>11.3f}"
            soles    = f"{float(prod.soles):>11.2f}"
            p.text(f"{nom_prod} {cantidad} {soles}\n")
        p.text("-" * MAX_SIZE + "\n")
        
        # --- SECCIÓN 2: DESGLOSE DE TOTALES POR TURNO ---
        p.set(align='center', bold=True)
        p.text("[ RESUMEN DE TURNOS ]\n\n")
        p.set(align='left', bold=False)
        p.text("TURNO/ISLA      OPERADOR          TOTAL\n")
        p.text("----------------------------------------\n")
        for t in turnos_dia:
            etiqueta_turno = f"T:{t.turno} / I:{t.isla}"[:15].ljust(15)
            operador = (t.operador if t.operador else "N/A")[:12].ljust(13)
            monto_turno = f"S/ {float(t.total):>9.2f}"
            p.text(f"{etiqueta_turno} {operador} {monto_turno}\n")
        p.text("-" * MAX_SIZE + "\n")

        # --- SECCIÓN 3: NUEVO - CONSOLIDADO DE DEPÓSITOS ---
        sum_total_depositos = 0.0
        if depositos_dia:
            p.set(align='center', bold=True)
            p.text("[ TOTAL DEPOSITOS DEL DIA ]\n\n")
            p.set(align='left', bold=False)
            p.text("CONCEPTO                          SOLES\n")
            p.text("----------------------------------------\n")
            for dep in depositos_dia:
                concepto = dep.concepto[:30].ljust(30)
                monto = f"{float(dep.total_monto):>9.2f}"
                sum_total_depositos += float(dep.total_monto)
                p.text(f"{concepto} {monto}\n")
            p.text("-" * MAX_SIZE + "\n")

        # --- SECCIÓN 4: NUEVO - CONSOLIDADO DE GASTOS ---
        sum_total_gastos = 0.0
        if gastos_dia:
            p.set(align='center', bold=True)
            p.text("[ TOTAL GASTOS DEL DIA ]\n\n")
            p.set(align='left', bold=False)
            p.text("CONCEPTO                          SOLES\n")
            p.text("----------------------------------------\n")
            for gas in gastos_dia:
                concepto = gas.concepto[:30].ljust(30)
                monto = f"{float(gas.total_monto):>9.2f}"
                sum_total_gastos += float(gas.total_monto)
                p.text(f"{concepto} {monto}\n")
            p.text("-" * MAX_SIZE + "\n")
        
        # --- SECCIÓN 5: CONSOLIDADO FINANCIERO (RESUMEN DE CAJAS) ---
        if pagos:
            p.set(align='center', bold=True)
            p.text("[ CONSOLIDADO FINANCIERO ]\n\n")
            p.set(align='left', bold=False)
            
            efectivo  = pagos.total_efectivo if pagos.total_efectivo else 0.0
            tarjeta   = pagos.total_tarjeta if pagos.total_tarjeta else 0.0
            yape      = pagos.total_yape if pagos.total_yape else 0.0
            sub_pagos = efectivo + tarjeta + yape
            
            p.text(f"TOTAL EFECTIVO     : S/ {float(efectivo):>12.2f}\n")
            p.text(f"TOTAL TARJETA      : S/ {float(tarjeta):>12.2f}\n")
            p.text(f"TOTAL YAPE/PLIN    : S/ {float(yape):>12.2f}\n")
            p.text("                     " + "   -------------\n")
            p.text(f"TOTAL BRUTO EN CAJA: S/ {float(sub_pagos):>12.2f}\n")
            p.text(f"(-) TOTAL DEPOSITOS: S/ {float(sum_total_depositos):>12.2f}\n")
            p.text(f"(-) TOTAL GASTOS   : S/ {float(sum_total_gastos):>12.2f}\n")
            p.text("-" * MAX_SIZE + "\n")
            
        # --- PIE DE TICKET (GRAN TOTAL NETO) ---
        p.set(align='right', bold=True, width=1, height=2)
        p.text(f"TOTAL NETO DIA: S/ {float(dia.total):.2f}\n\n")
        
        p.set(align='center', bold=False, width=1, height=1)
        p.text("--- FIN DE REPORTE DIARIO ---\n")
        p.text("Uso exclusivo operativo e interno.\n\n\n")
        
        p.cut()

        if p.device:
            from usb.util import dispose_resources
            dispose_resources(p.device)        
        return True

    except Exception as e:
        print(f"❌ Error al generar ticket completo de cierre de día: {e}")
        return False
    finally:
        if 'conn' in locals() and conn: 
            conn.close()
        if p: 
            del p
        time.sleep(1)
# --- LANZADOR PRINCIPAL ---
if __name__ == "__main__":
    print("--- INICIANDO SERVICIOS MULTI-HILO ---")
    
    # Crear los hilos
    hilo_print = threading.Thread(target=proceso_impresion, name="HiloImpresion")
    hilo_cierres = threading.Thread(target=proceso_cierres, name="HiloCierres")

    # Iniciar los hilos
    hilo_print.start()
    hilo_cierres.start()

    # Mantener el programa principal vivo
    hilo_print.join()
    hilo_cierres.join()