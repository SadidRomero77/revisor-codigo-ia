from ejemplo.app import descuento

def test_sin_descuento():
    assert descuento(100, 0) == 100

def test_diez_por_ciento():
    assert descuento(100, 10) == 90
