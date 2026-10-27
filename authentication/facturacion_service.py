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
            "iTipCont": 1,
            "dNumTim": "02595733",
            "dEst": "001",
            "dPunExp": "003"
        }
        user_emis = transaccion_data.get("emis", {})
        merged_emis = {**default_emis, **user_emis}

        de_data = transaccion_data.get("DE", {
            "dVerFor": 150,
            "gTasaCambio": transaccion_data.get("tasa_cambio", 7300),
            "gEmis": merged_emis,
            "gDatGralOpe": transaccion_data.get("dat_gral_ope", {}),
            "gDtipDE": transaccion_data.get("dtip_de", {})
        })
        if "gEmis" in de_data and isinstance(de_data["gEmis"], dict):
            de_data["gEmis"] = {**default_emis, **de_data["gEmis"]}

        payload = {
            "operation": "generar_de",
            "operacion": "generar_de",
            "params": {
                "DE": de_data
            }
        }
        
        try:
            response = requests.post(esi_url, json=payload, headers=headers, timeout=30)
            data = response.json()
            if response.status_code == 200:
                return data
            logger.error(f"Error al generar DE en SIFEN: {response.status_code} - {response.text}")
            return {"code": response.status_code, "error": response.text}
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
            "operacion": "get_estado_sifen",
            "params": {
                "CDC": cdc,
                "dRucEm": d_ruc_em
            }
        }
        
        try:
            response = requests.post(esi_url, json=payload, headers=headers, timeout=30)
            data = response.json()
            if response.status_code == 200:
                return data
            logger.error(f"Error al consultar estado SIFEN: {response.status_code} - {response.text}")
            return {"code": response.status_code, "error": response.text}
        except requests.exceptions.RequestException as e:
            logger.error(f"Excepción de red al consultar estado SIFEN: {str(e)}")
            return {"code": -1, "error": str(e)}

    @classmethod
    def descargar_kude(cls, auth_token, cdc):
        """
        Descarga el KuDE (PDF) correspondiente a un CDC utilizando petición HTTP GET.
        
        Args:
            auth_token (str): Token de autenticación activo.
            cdc (str): Código de Control (CDC).
            
        Returns:
            bytes o None: Contenido binario del PDF del KuDE o None si falla.
        """
        base_url = cls._get_base_url()
        url = f"{base_url}/misife00/v1/esi/kude/{cdc}"
        headers = {
            'accept': 'application/pdf',
            'Authentication-Token': auth_token
        }
        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.content
            logger.error(f"Error al descargar KuDE: {response.status_code} - {response.text}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Excepción de red al descargar KuDE: {str(e)}")
            return None

    @classmethod
    def descargar_xml(cls, auth_token, cdc):
        """
        Descarga el archivo XML firmado correspondiente a un CDC utilizando petición HTTP GET.
        
        Args:
            auth_token (str): Token de autenticación activo.
            cdc (str): Código de Control (CDC).
            
        Returns:
            str o None: Contenido XML o None si falla.
        """
        base_url = cls._get_base_url()
        url = f"{base_url}/misife00/v1/esi/xml/{cdc}"
        headers = {
            'accept': 'application/xml',
            'Authentication-Token': auth_token
        }
        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.text
            logger.error(f"Error al descargar XML: {response.status_code} - {response.text}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Excepción de red al descargar XML: {str(e)}")
            return None
