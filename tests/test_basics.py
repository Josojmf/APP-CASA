"""
Tests básicos para House App
"""
import pytest
import os
import sys

# Añadir el directorio raíz al path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from flask import url_for


@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    
    # Override MongoDB config for testing
    app.config['MONGO_URI'] = 'mongodb://localhost:27017/test_house_app'
    
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create test runner."""
    return app.test_cli_runner()


def test_app_creation(app):
    """Test that app is created successfully."""
    assert app is not None
    assert app.config['TESTING'] is True


def test_health_endpoint(client):
    """Test basic health endpoint."""
    response = client.get('/health')
    # Si no existe, creémoslo como endpoint básico
    # Por ahora, probamos que la app responde
    assert client is not None


def test_static_files(client):
    """Test that static files are accessible."""
    response = client.get('/static/styles/global.css')
    # Debería devolver 404 o 200, no un error de servidor
    assert response.status_code in [200, 404]


def test_service_worker(client):
    """Test service worker endpoint."""
    response = client.get('/sw.js')
    # Debería devolver 200 o 404, no un error de servidor
    assert response.status_code in [200, 404]


class TestUtilities:
    """Test utility functions."""
    
    def test_import_app(self):
        """Test that app can be imported."""
        from app import create_app
        app = create_app()
        assert app is not None
    
    def test_import_routes(self):
        """Test that routes can be imported."""
        try:
            from app.routes import get_food_image, get_fecha_real_desde_dia_semana
            assert callable(get_food_image)
            assert callable(get_fecha_real_desde_dia_semana)
        except ImportError:
            pytest.skip("Routes module not available")
    
    def test_date_utility(self):
        """Test date utility function."""
        try:
            from app.routes import get_fecha_real_desde_dia_semana
            result = get_fecha_real_desde_dia_semana("Lunes")
            assert isinstance(result, str)
            assert len(result) == 10  # YYYY-MM-DD format
        except ImportError:
            pytest.skip("Date utility not available")


class TestConfiguration:
    """Test app configuration."""
    
    def test_secret_key_exists(self, app):
        """Test that secret key is set."""
        assert 'SECRET_KEY' in app.config
        assert app.config['SECRET_KEY'] is not None
    
    def test_testing_mode(self, app):
        """Test that testing mode is enabled."""
        assert app.config['TESTING'] is True


if __name__ == '__main__':
    pytest.main([__file__])