#!/usr/bin/env python
"""
Test SMTP Connection to Gmail
Diagnóstico independiente de Django para verificar credenciales y conectividad
"""
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuración
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465  # SSL
EMAIL_USER = "lcastillos1@unemi.edu.ec"
EMAIL_PASSWORD = "fifx vmhs lirb aext"
RECIPIENT = "lcastillos1@unemi.edu.ec"  # Enviar a ti mismo como prueba

print(f"🔍 Probando conexión SMTP a {SMTP_SERVER}:{SMTP_PORT}...")
print(f"📧 Usuario: {EMAIL_USER}")

try:
    # Crear contexto SSL
    context = ssl.create_default_context()
    
    # Conectar usando SSL (puerto 465)
    print("\n1️⃣ Estableciendo conexión SSL...")
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=30) as server:
        print("   ✅ Conexión SSL establecida")
        
        # Login
        print("\n2️⃣ Autenticando...")
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        print("   ✅ Autenticación exitosa")
        
        # Crear mensaje de prueba
        print("\n3️⃣ Enviando correo de prueba...")
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = RECIPIENT
        msg['Subject'] = "✅ Test SMTP - Cronograma"
        
        body = """
        Este es un correo de prueba del sistema de notificaciones.
        
        Si recibes este mensaje, significa que:
        ✅ Las credenciales de Gmail son correctas
        ✅ El puerto 465 (SSL) funciona
        ✅ El problema está en la configuración de Django
        
        Enviado desde: test_smtp.py
        """
        msg.attach(MIMEText(body, 'plain'))
        
        server.send_message(msg)
        print("   ✅ Correo enviado exitosamente")
        
    print("\n" + "="*60)
    print("🎉 ¡ÉXITO! La conexión SMTP funciona perfectamente.")
    print("="*60)
    print("\n💡 Conclusión:")
    print("   - Las credenciales de Gmail son válidas")
    print("   - El puerto 465 está abierto")
    print("   - El problema está en Django, no en la red")
    print("\n📝 Próximo paso:")
    print("   - Revisar configuración de timeout en Django")
    print("   - Verificar que Django esté usando SSL correctamente")
    
except smtplib.SMTPAuthenticationError as e:
    print(f"\n❌ ERROR DE AUTENTICACIÓN: {e}")
    print("\n💡 Posibles causas:")
    print("   1. Contraseña de aplicación incorrecta")
    print("   2. Verificación en 2 pasos no activada en Gmail")
    print("   3. Acceso de aplicaciones menos seguras bloqueado")
    
except smtplib.SMTPException as e:
    print(f"\n❌ ERROR SMTP: {e}")
    
except TimeoutError as e:
    print(f"\n❌ TIMEOUT: {e}")
    print("\n💡 Esto significa:")
    print("   - Tu firewall/antivirus está bloqueando la conexión")
    print("   - O tu ISP bloquea el puerto 465")
    
except Exception as e:
    print(f"\n❌ ERROR INESPERADO: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
