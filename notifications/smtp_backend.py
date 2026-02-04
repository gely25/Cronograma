"""
ULTRA-RESILIENT SMTP Backend para Gmail con SSL directo
- Fuerza IPv4 para evitar timeouts de IPv6 en Windows.
- Fuerza Puerto 465 si se detecta puerto 587 (SSL no funciona en 587).
- Diagnóstico de socket previo a la conexión.
- Logging exhaustivo en c:\\Cronograma\\smtp_diagnosis.log
"""
import smtplib
import ssl
import socket
import os
import json
from django.core.mail.backends.base import BaseEmailBackend
from django.conf import settings

class DirectSSLEmailBackend(BaseEmailBackend):
    def __init__(self, host=None, port=None, username=None, password=None,
                 use_tls=None, fail_silently=False, use_ssl=None, timeout=None,
                 ssl_keyfile=None, ssl_certfile=None, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.host = host or getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com')
        
        # FUERZA: Si es SSL directo, el puerto DEBE ser 465 o similar. 587 nunca funcionará con SMTP_SSL.
        raw_port = port or getattr(settings, 'EMAIL_PORT', 465)
        try:
            self.port = int(raw_port)
        except:
            self.port = 465
            
        if self.port == 587:
            self.port = 465 # Corrección automática
            
        self.username = username or getattr(settings, 'EMAIL_HOST_USER', '')
        self.password = password or getattr(settings, 'EMAIL_HOST_PASSWORD', '')
        self.timeout = timeout or 60
        self.connection = None
        self.log_file = "c:\\Cronograma\\smtp_diagnosis.log"

    def log(self, msg):
        try:
            with open(self.log_file, "a", encoding='utf-8') as f:
                f.write(f"DEBUG: {msg}\n")
        except:
            pass

    def open(self):
        if self.connection:
            return False
            
        self.log(f"\n=== NUEVA CONEXIÓN: {self.host}:{self.port} ===")
        self.log(f"User: {self.username}")
        
        try:
            # 1. DIAGNÓSTICO DE RED (Ping de socket básico)
            self.log(f"Provando reachability de {self.host}:{self.port} via socket...")
            try:
                # Forzar IPv4 en el test de socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((self.host, self.port))
                sock.close()
                self.log("Socket TCP básico: OK (Puerto abierto)")
            except Exception as se:
                self.log(f"Socket TCP básico: FALLO - {str(se)}")
                # Si esto falla, el firewall o ISP está bloqueando el puerto 465.
            
            # 2. CONEXIÓN SSL
            self.log("Iniciando smtplib.SMTP_SSL con context unverified...")
            context = ssl._create_unverified_context()
            
            # Monkeypatch socket para forzar IPv4 globalmente durante esta llamada
            old_getaddrinfo = socket.getaddrinfo
            def new_getaddrinfo(*args, **kwargs):
                responses = old_getaddrinfo(*args, **kwargs)
                return [r for r in responses if r[0] == socket.AF_INET]
            
            socket.getaddrinfo = new_getaddrinfo
            
            try:
                self.connection = smtplib.SMTP_SSL(
                    self.host, 
                    self.port, 
                    context=context, 
                    timeout=self.timeout
                )
            finally:
                socket.getaddrinfo = old_getaddrinfo # Restaurar
            
            self.log("Conexión SSL establecida.")

            # 3. LOGIN
            if self.username and self.password:
                self.log("Intentando Login...")
                self.connection.login(self.username, self.password)
                self.log("Login EXITOSO.")
            
            return True
        except Exception as e:
            self.log(f"ERROR FATAL: {type(e).__name__} - {str(e)}")
            if not self.fail_silently:
                raise
            return False

    def close(self):
        if not self.connection:
            return
        try:
            self.connection.quit()
        except Exception:
            pass
        finally:
            self.connection = None

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        new_conn_created = self.open()
        if not self.connection:
            return 0
        num_sent = 0
        try:
            for message in email_messages:
                try:
                    # Preparar destinatarios
                    recipients = message.recipients()
                    if not recipients:
                        continue
                        
                    self.log(f"Enviando a: {recipients}")
                    self.connection.sendmail(
                        message.from_email,
                        recipients,
                        message.message().as_bytes()
                    )
                    num_sent += 1
                except Exception as ex:
                    self.log(f"Error enviando mensaje: {str(ex)}")
                    if not self.fail_silently:
                        raise
        finally:
            if new_conn_created:
                self.close()
        return num_sent
