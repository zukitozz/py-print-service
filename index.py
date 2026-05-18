import pyodbc
import time
import os
import requests
import threading
from dotenv import load_dotenv
from escpos.printer import Usb
import json

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
                    SELECT id, CASE tipo_comprobante WHEN '01' THEN 'FACTURA ELECTRONICA' WHEN '03' THEN 'BOLETA ELECTRONICA' WHEN '07' THEN 'NOTA DE CREDITO ELECTRONICA' WHEN '08' THEN 'NOTA DE DEBITO ELECTRONICA' WHEN '50' THEN 'NOTA DE DESPACHO' WHEN '51' THEN 'CALIBRACION' WHEN '52' THEN 'NOTA INTERNA' END as tipo_comprobante, numeracion_comprobante, placa, fecha_emision, gravadas, total, igv, cadena_para_codigo_qr, codigo_hash, ReceptorId 
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

                    # La función imprimir_comprobante usará comp.cadena_para_codigo_qr 
                    # si ya existe (porque el hilo 1 terminó) o el QR por defecto (si no ha terminado)
                    if imprimir_comprobante(comp, items, receptor):
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
                SELECT id, total, fecha, turno, isla, efectivo, tarjeta, yape, CierrediaId 
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

                    cursor.execute("SELECT * FROM Depositos WHERE CierreturnoId = ?", turno.id)
                    depositos = cursor.fetchall()

                    cursor.execute("SELECT * FROM Gastos WHERE CierreturnoId = ?", turno.id)
                    gastos = cursor.fetchall()
                    
                    print(f"🖨️ Imprimiendo Cierre Turno: {turno.turno} - Isla: {turno.isla}")
                    if imprimir_ticket_cierre_turno(turno, detalles, depositos, gastos):
                        cursor.execute("UPDATE Cierreturnos SET impresion = 1 WHERE id = ?", turno.id)
                        conn.commit()

            # 2. Opcional: Buscar Cierres de Día (si estado es 'CERRADO' e impresion = 0)
            cursor.execute("SELECT * FROM Cierredias WHERE impresion = 0 OR impresion IS NULL")
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

def imprimir_comprobante(comprobante, items, receptor):
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
        p.text("-" * 40 + "\n")

        # Datos del Cliente
        p.set(align='left')
        p.text(f"CLIENTE: {receptor.razon_social[:30]}\n")
        p.text(f"{receptor.tipo_documento}: {receptor.numero_documento}\n")
        if receptor.tipo_documento == "RUC":
            p.text(f"DIRECCION: {receptor.direccion[:30]}\n")  
        if comprobante.placa:
            p.text(f"PLACA: {comprobante.placa}\n")
        p.text(f"FECHA: {comprobante.fecha_emision}\n")
        p.text("-" * 40 + "\n")

        # Detalle de Items
        p.text("CANT  DESCRIPCION         PRECIO   TOTAL\n")
        for item in items:
            desc = item.descripcion[:18].ljust(18)
            cant = str(item.cantidad).ljust(5)
            prec = f"{item.precio:>7.2f}"
            tot = f"{item.valor_venta:>7.2f}"
            p.text(f"{cant} {desc} {prec} {tot}\n")

        # Totales
        p.text("-" * 40 + "\n")
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

def imprimir_ticket_cierre_turno(cierre, detalles, depositos, gastos):
    p = None
    try:
        sum_depositos = sum(float(d.monto) for d in depositos)
        sum_gastos = sum(float(g.monto) for g in gastos)
        total_turno = float(cierre.total) - sum_depositos - sum_gastos
        p = Usb(ID_VENDOR, ID_PRODUCT, profile="TM-T88V")
        p.set(align='center', bold=True)
        p.text("REPORTE DE CIERRE DE TURNO\n")
        p.text(f"{EMP_CONFIG['rs']}\n")
        p.text("-" * 43 + "\n")
        
        p.set(align='left', bold=False)
        p.text(f"FECHA: {cierre.fecha}\n")
        p.text(f"TURNO: {cierre.turno}   ISLA: {cierre.isla}\n")
        p.text("-" * 43 + "\n")
        
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

        p.text("-" * 43 + "\n")
        p.set(align='center', bold=True)
        p.text("DEPOSITOS\n")
        p.set(align='left', bold=False)
        p.text("CONCEPTO                        SOLES\n")
        for d in depositos:
            prod = d.concepto[:27].ljust(28)
            cant = f"{float(d.monto):>9.2f}"
            p.text(f"{prod} {cant}\n")

        p.text("-" * 43 + "\n")
        p.set(align='center', bold=True)
        p.text("GASTOS\n")
        p.set(align='left', bold=False)
        p.text("CONCEPTO                        SOLES\n")
        for d in gastos:
            prod = d.concepto[:27].ljust(28)
            cant = f"{float(d.monto):>9.2f}"
            p.text(f"{prod} {cant}\n")            


        p.text("-" * 43 + "\n")
        p.set(align='right')
        p.text(f"EFECTIVO: S/ {cierre.efectivo:>9.2f}\n")
        p.text(f"TARJETA:  S/ {cierre.tarjeta:>9.2f}\n")
        p.text(f"YAPE/PLIN: S/ {cierre.yape:>9.2f}\n")
        p.set(bold=True)
        p.text(f"SUB TOTAL TURNO: S/ {cierre.total:>9.2f}\n")
        p.text(f"DEPOSITOS: S/ {sum_depositos:>9.2f}\n")
        p.text(f"GASTOS: S/ {sum_gastos:>9.2f}\n")
        p.text(f"TOTAL TURNO: S/ {total_turno:>9.2f}\n")

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
    # Lógica similar a la anterior pero consolidando datos del día
    p = None
    try:
        p = Usb(ID_VENDOR, ID_PRODUCT, profile="TM-T88V")
        p.set(align='center', bold=True, width=2, height=2)
        p.text("CIERRE DIARIO\n")
        p.set(bold=False, width=1, height=1)
        p.text(f"FECHA: {dia.fecha}\n")
        p.text("-" * 40 + "\n")
        p.set(align='right', bold=True)
        p.text(f"TOTAL DEL DIA: S/ {dia.total:.2f}\n")
        p.cut()

        if p.device:
            from usb.util import dispose_resources
            dispose_resources(p.device)        
        return True
    except Exception as e:
        print(f"Error imprimir cierre dia: {e}")
        return False
    finally:
        if p: del p
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