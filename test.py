from escpos.printer import Usb

def imprimir_ticket_ejemplo():
    try:
        # 1. Configurar la impresora
        # Los valores 0x04b8 y 0x0202 son estándar para Epson
        # profile="TM-T88V" ayuda a gestionar el ancho del papel correctamente
        p = Usb(0x04b8, 0x0202, profile="TM-T88V")

        # 2. Encabezado
        p.set(align='center', font='a', bold=True, width=2, height=2)
        p.text("MI GASOLINERA\n")
        
        p.set(align='center', bold=False, width=1, height=1)
        p.text("RUC: 12345678901\n")
        p.text("Av. Principal 123, Lima\n")
        p.text("-" * 40 + "\n")

        # 3. Contenido del Ticket
        p.set(align='left')
        p.text("PRODUCTO           CANT    IMPORTE\n")
        p.text("G. REGULAR 90      10.5    S/ 180.00\n")
        p.text("ACEITE MOTOR 1L     1.0    S/  35.00\n")
        
        # 4. Totales
        p.text("-" * 40 + "\n")
        p.set(align='right', bold=True)
        p.text("TOTAL: S/ 215.00\n\n")

        # 5. Código QR o Código de Barras (Opcional)
        p.set(align='center')
        p.qr("https://tusitio.com/factura/123", size=8)
        p.text("\nGracias por su compra\n")

        # 6. Finalización: Alimentar papel y Cortar
        p.cut()
        print("Ticket enviado con éxito.")

    except Exception as e:
        print(f"Error al imprimir: {e}")

if __name__ == "__main__":
    imprimir_ticket_ejemplo()