import requests
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class FacturaSeguraService:
    """
    Servicio de integración con la API de Facturación Electrónica SIFEN (Factura Segura - ESI).
    Maneja autenticación, generación de documentos electrónicos (DE), consulta de estado y descarga de KuDE/XML.
    """
    
    @classmethod
    def _get_base_url(cls):
        """
        Obtiene la URL base del servicio de Factura Segura desde la configuración de Django (settings).
        """
        return getattr(settings, 'SIFEN_API_BASE_URL', 'https://apitest.facturasegura.com.py')

    @staticmethod
    def _respuesta_error(response):
        """
        Convierte una respuesta HTTP fallida en el formato de error del servicio.

        Args:
            response: Respuesta HTTP distinta de 200.

        Returns:
            dict: Código HTTP, descripción legible y cuerpo original de la respuesta.
        """
        try:
            errores = response.json().get('response', {}).get('errors') or []
        except (ValueError, AttributeError):
            errores = []
        descripcion = ', '.join(str(e) for e in errores) or f"HTTP {response.status_code}"
        return {"code": response.status_code, "description": descripcion, "error": response.text}

    @classmethod
    def obtener_token(cls, email=None, password=None):
        """
        Se conecta al endpoint de login para obtener el authentication_token.
        Utiliza credenciales de settings o variables de entorno si no se proveen explícitamente.
        
        Args:
            email (str): Correo electrónico de acceso a Factura Segura.
            password (str): Contraseña de acceso.
            
        Returns:
            str: Token de autenticación si es exitoso, None en caso contrario.
        """
        email = email or getattr(settings, 'SIFEN_API_EMAIL', 'test@facturasegura.com.py')
        password = password or getattr(settings, 'SIFEN_API_PASSWORD', 'testpassword')
        
        base_url = cls._get_base_url()
        login_url = f"{base_url}/login?include_auth_token"
        
        headers = {
            'accept': 'application/json',
            'Content-Type': 'application/json'
        }
        payload = {
            'email': email,
            'password': password
        }
        
        try:
            response = requests.post(login_url, json=payload, headers=headers, timeout=30)
            data = response.json()
            if response.status_code == 200:
                resp_dict = data.get('response', {})
                token = (
                    data.get('authentication_token') or 
                    data.get('auth_token') or 
                    data.get('token') or 
                    resp_dict.get('authentication_token') or
                    resp_dict.get('user', {}).get('authentication_token')
                )
                if not token:
                    logger.error("Login SIFEN sin authentication_token en la respuesta")
                return token
            logger.error(f"Error en autenticación SIFEN: {response.status_code} - {response.text}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Excepción de red al autenticar con SIFEN: {str(e)}")
            return None

    @classmethod
    def generar_de(cls, auth_token, transaccion_data):
        """
        Construye el payload JSON exigido por Factura Segura y envía la operación 'generar_de'
        para procesar la transacción de compra/venta de divisas.
        
        Args:
            auth_token (str): Token de autenticación activo.
            transaccion_data (dict): Datos de la transacción de divisas completada.
            
        Returns:
            dict: Respuesta con el CDC generado si es exitoso (code: 0) o detalles del error.
        """
        base_url = cls._get_base_url()
        esi_url = f"{base_url}/misife00/v1/esi"
        
        headers = {
            'accept': 'application/json',
            'Content-Type': 'application/json',
            'Authentication-Token': auth_token
        }
        
        default_emis = {
            "dRucEm": "2595733",
            "dDVEmi": "3",
            "iTipCont": "1",
            "dNumTim": "02595733",
            "dFeIniT": "2025-03-27",
            "dFelIniT": "2025-03-27",
            "dEst": "001",
            "dPunExp": "003",
            "dTelEmi": "(021)5887000"
        }
        user_emis = transaccion_data.get("emis", {})
        merged_emis = {**default_emis, **user_emis}

        de_data = transaccion_data.get("DE", {
            "dVerFor": 150,
            "gEmis": merged_emis,
            "gDatGralOpe": transaccion_data.get("dat_gral_ope", {}),
            "gDtipDE": transaccion_data.get("dtip_de", {})
        })
        if "gEmis" in de_data and isinstance(de_data["gEmis"], dict):
            de_data["gEmis"] = {**default_emis, **de_data["gEmis"]}

        if isinstance(de_data, dict) and "dEmailRec" not in de_data:
            de_data["dEmailRec"] = transaccion_data.get("dEmailRec", "soporte@facturasegura.com.py")

        if isinstance(de_data, dict) and "gActEco" not in de_data:
            de_data["gActEco"] = getattr(settings, 'FACTURASEGURA_EMISOR_ACTIVIDADES_ECONOMICAS', [
                {
                    'cActEco': '62010',
                    'dDesActEco': 'Actividades de programación informática'
                },
                {
                    'cActEco': '74909',
                    'dDesActEco': 'Otras actividades profesionales, científicas y técnicas n.c.p.'
                }
            ])



        payload = {
            "operation": "generar_de",
            "params": {
                "DE": de_data
            }
        }
        
        try:
            logger.debug("Payload enviado a SIFEN: %s", payload)
            response = requests.post(esi_url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            logger.error(f"Error al generar DE en SIFEN: {response.status_code} - {response.text}")
            return cls._respuesta_error(response)
        except requests.exceptions.RequestException as e:
            logger.error(f"Excepción de red al generar DE en SIFEN: {str(e)}")
            return {"code": -1, "error": str(e)}

    @classmethod
    def get_estado_sifen(cls, auth_token, cdc, d_ruc_em):
        """
        Consulta el estado de un Documento Electrónico en el SIFEN mediante la operación 'get_estado_sifen'.
        
        Args:
            auth_token (str): Token de autenticación activo.
            cdc (str): Código de Control (CDC) de la factura electrónica.
            d_ruc_em (str): RUC del emisor.
            
        Returns:
            dict: Estado del documento devuelto por SIFEN.
        """
        base_url = cls._get_base_url()
        esi_url = f"{base_url}/misife00/v1/esi"
        
        headers = {
            'accept': 'application/json',
            'Content-Type': 'application/json',
            'Authentication-Token': auth_token
        }
        payload = {
            "operation": "get_estado_sifen",
            "params": {
                "CDC": cdc,
                "dRucEm": d_ruc_em
            }
        }
        
        try:
            response = requests.post(esi_url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            logger.error(f"Error al consultar estado SIFEN: {response.status_code} - {response.text}")
            return cls._respuesta_error(response)
        except requests.exceptions.RequestException as e:
            logger.error(f"Excepción de red al consultar estado SIFEN: {str(e)}")
            return {"code": -1, "error": str(e)}

    @classmethod
    def descargar_kude(cls, auth_token, cdc):
        """
        Descarga el KuDE (PDF) oficial correspondiente a un CDC utilizando petición HTTP GET
        al endpoint oficial de Factura Segura.
        
        Args:
            auth_token (str): Token de autenticación activo.
            cdc (str): Código de Control (CDC).
            
        Returns:
            bytes: Contenido binario del PDF del KuDE si es exitoso, None en caso contrario.
        """
        base_url = cls._get_base_url()
        url = f"{base_url}/misife00/v1/esi/dwn_kude/2595733/{cdc}"
        headers = {
            'accept': 'application/pdf',
            'Authentication-Token': auth_token
        }
        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.content
            logger.error(f"Error al descargar KuDE de SIFEN: {response.status_code} - {response.text}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Excepción de red al descargar KuDE de SIFEN: {str(e)}")
            return None

    @classmethod
    def descargar_xml(cls, auth_token, cdc):
        """
        Descarga el archivo XML firmado correspondiente a un CDC utilizando petición HTTP GET.
        
        Args:
            auth_token (str): Token de autenticación activo.
            cdc (str): Código de Control (CDC).
            
        Returns:
            str: Contenido XML.
        """
        base_url = cls._get_base_url()
        url = f"{base_url}/misife00/v1/esi/dwn_xml/2595733/{cdc}"
        headers = {
            'accept': 'application/xml',
            'Authentication-Token': auth_token
        }
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                return response.text
            logger.error(f"Error al descargar XML oficial de SIFEN: {response.status_code} - {response.text}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Excepción de red al descargar XML de SIFEN: {str(e)}")
            return None
