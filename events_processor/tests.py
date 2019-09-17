import unittest

from tests.basic import DetectionTestCase

if __name__ == '__main__':
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(DetectionTestCase))

    runner = unittest.TextTestRunner()
    runner.run(suite)
