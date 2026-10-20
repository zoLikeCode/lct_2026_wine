"""The public product has one light UI and a redirect for old light links."""
import re
import unittest
import test_server


class DesignRoutesTest(unittest.TestCase):
    setUp = test_server.ServerTest.setUp
    tearDown = test_server.ServerTest.tearDown

    def test_single_design_assets_and_shared_profile(self):
        page = self.c.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('no-cache', page.headers['cache-control'])
        for asset in re.findall(r'(?:href|src)="([^"]+\?v=[a-f0-9]+)"', page.text):
            self.assertEqual(self.c.get(asset).status_code, 200, asset)
        self.assertIn('id="assistant-view"', page.text)
        self.assertIn('class="brand" href="/"', page.text)
        self.assertIn('href="/app.css?', page.text)
        self.assertIn('--bg:#f8f5ef', self.c.get('/app.css').text)

        for route in ['/old-design', '/old-design/']:
            redirect = self.c.get(route, follow_redirects=False)
            self.assertEqual(redirect.status_code, 308)
            self.assertEqual(redirect.headers['location'], '/')
        for route in ['/dark-design', '/dark-design/', '/dark-design/manifest.webmanifest']:
            self.assertEqual(self.c.get(route).status_code, 404)

        manifest = self.c.get('/manifest.webmanifest').json()
        self.assertEqual(manifest['start_url'], '/')
        self.c.get('/api/product/bootstrap')
        self.c.post('/api/product/preferences/merlo', json={'rating':4, 'favorite':True})
        self.c.get('/old-design')
        self.assertEqual(self.c.get('/api/product/bootstrap').json()['preferences']['merlo']['rating'], 4)
        self.assertEqual(self.c.get('/admin/api/history').status_code, 401)
        sw = self.c.get('/sw.js').text
        for path in ['/app.css', '/chat.css', '/manifest.webmanifest']:
            self.assertIn("'" + path + "'", sw)
        self.assertNotIn('/dark-design', sw)


if __name__ == '__main__':
    unittest.main()
