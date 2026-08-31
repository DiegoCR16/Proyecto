from django.test import TestCase, Client
from django.contrib.auth.models import User
from authentication.models import UserProfile, AuditLog, Role

class SegmentationPSE6Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-6:
    Clasificación y Segmentación Base de Clientes (Epic: Gestión de Clientes).
    Valida asignación de categorías (Minorista, Corporativo, VIP), validación de coherencia
    por volumen transaccional en guaraníes y naturaleza (Física/Jurídica), consultas,
    actualizaciones en ficha y registro de logs de auditoría por usuario administrativo.
    """

    def setUp(self):
        """
        Configuración inicial de usuarios, roles y perfiles para PSE-6.
        """
        self.client = Client()
        self.admin_role, _ = Role.objects.get_or_create(name="Admin")
        self.corp_role, _ = Role.objects.get_or_create(name="Corporate")
        self.ind_role, _ = Role.objects.get_or_create(name="Cliente")

        # Administrador
        self.admin_user = User.objects.create_user(username="admin_pse6", email="admin@globalexchange.com", password="Password123*")
        self.admin_profile = UserProfile.objects.create(
            user=self.admin_user,
            role=self.admin_role,
            category='CORPORATIVO',
            is_corporate=True
        )

        # Cliente Persona Física
        self.fisica_user = User.objects.create_user(username="juan_perez", email="juan@globalexchange.com", password="Password123*")
        self.fisica_profile = UserProfile.objects.create(
            user=self.fisica_user,
            role=self.ind_role,
            category='MINORISTA',
            is_corporate=False,
            ci_ruc='1234567',
            transaction_volume=10000000 # 10 Millones Gs
        )

        # Cliente Persona Jurídica
        self.juridica_user = User.objects.create_user(username="empresa_sa", email="empresa@globalexchange.com", password="Password123*")
        self.juridica_profile = UserProfile.objects.create(
            user=self.juridica_user,
            role=self.corp_role,
            category='CORPORATIVO',
            is_corporate=True,
            ci_ruc='80011122-3',
            transaction_volume=80000000 # 80 Millones Gs
        )

        self.list_url = '/auth/admin/clients/'
        self.detail_url = f'/auth/admin/clients/{self.fisica_user.id}/'

    def test_default_category_is_minorista(self):
        """
        Verifica que por defecto la categoría inicial de un cliente sea Minorista.
        """
        new_user = User.objects.create_user(username="nuevo_cliente", email="nuevo@globalexchange.com", password="Password123*")
        new_profile = UserProfile.objects.create(user=new_user, is_corporate=False)
        self.assertEqual(new_profile.category, 'MINORISTA')

    def test_validation_coherence_fisica_cannot_be_corporativo(self):
        """
        Valida que un cliente de naturaleza Física no pueda ser clasificado como Corporativo.
        """
        with self.assertRaises(ValueError):
            self.fisica_profile.clean_category_assignment('CORPORATIVO', 10000000)

    def test_validation_coherence_vip_requires_minimum_volume(self):
        """
        Valida que la asignación de la categoría VIP requiera un volumen transaccional mínimo de 50.000.000 Gs.
        """
        with self.assertRaises(ValueError):
            self.fisica_profile.clean_category_assignment('VIP', 40000000)

        res = self.fisica_profile.clean_category_assignment('VIP', 60000000)
        self.assertTrue(res)

    def test_admin_client_list_view_access_and_filtering(self):
        """
        Verifica que el administrador pueda acceder al listado de clientes y filtrar por categoría.
        """
        self.client.force_login(self.admin_user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "juan_perez")
        self.assertContains(response, "empresa_sa")

        response_minorista = self.client.get(self.list_url, {'category': 'MINORISTA'})
        self.assertEqual(response_minorista.status_code, 200)
        self.assertContains(response_minorista, "juan_perez")
        self.assertNotContains(response_minorista, "empresa_sa")

    def test_admin_client_update_category_and_audit_log(self):
        """
        Verifica que el administrador pueda actualizar la categoría y volumen transaccional
        de un cliente, reflejándose inmediatamente en la base de datos y registrando un log de auditoría.
        """
        self.client.force_login(self.admin_user)

        response = self.client.post(self.detail_url, {
            'category': 'VIP',
            'transaction_volume': '75000000'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Categoría y volumen transaccional actualizados exitosamente")

        self.fisica_profile.refresh_from_db()
        self.assertEqual(self.fisica_profile.category, 'VIP')
        self.assertEqual(float(self.fisica_profile.transaction_volume), 75000000.0)

        audit_entry = AuditLog.objects.filter(action="CLIENT_CATEGORY_UPDATE", user=self.admin_user).first()
        self.assertIsNotNone(audit_entry)
        self.assertIn("admin_pse6", audit_entry.details)
        self.assertIn("VIP", audit_entry.details)
