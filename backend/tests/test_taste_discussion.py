import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).parent))
import test_server


class TasteDiscussionTest(unittest.TestCase):
    setUp = test_server.ServerTest.setUp
    tearDown = test_server.ServerTest.tearDown

    def test_guide_is_persistent_per_profile_and_does_not_complete_quiz(self):
        self.c.post('/api/product/account/register',json={'username':'guide-owner','password':'Local-test-password-456'})
        self.assertFalse(self.c.get('/api/product/taste').json()['guide_seen'])
        for _ in range(2):
            r = self.c.post('/api/product/taste/guide', json={})
            self.assertEqual(r.status_code, 200, r.text)
        with TestClient(self.app) as reopened:
            reopened.cookies.update(self.c.cookies)
            state = reopened.get('/api/product/taste').json()
            self.assertTrue(state['guide_seen'])
            self.assertFalse(state['quiz_complete'])
            self.assertEqual(reopened.get('/api/product/taste/swipe').status_code, 409)
        with TestClient(self.app) as other:
            other.post('/api/product/account/register',json={'username':'guide-other','password':'Local-test-password-456'})
            self.assertFalse(other.get('/api/product/taste').json()['guide_seen'])
        self.assertEqual(self.c.post('/api/product/taste/guide', json={},
                                    headers={'Origin': 'https://evil.example'}).status_code, 403)
        recs = self.app.state.product.recs
        recs.acknowledge_guide('guest')
        with self.store.db() as db:
            recs.merge_guest(db, 'guest', 'account')
        self.assertTrue(recs.guide_seen('account'))
        self.assertFalse(recs.guide_seen('guest'))

    def test_discussion_uses_current_profile_evidence_and_keeps_followups(self):
        self.c.post('/api/product/account/register', json={'username': 'taste.qa', 'password': 'Taste-discussion-only-2026'})
        quiz = self.c.get('/api/product/taste/quiz').json()
        answers = {q['id']: q['options'][0]['id'] for q in quiz['questions']}
        self.c.post('/api/product/taste/quiz', json={'answers': answers})
        self.c.post('/api/product/preferences/merlo', json={'rating': 5})
        with TestClient(self.app) as other:
            other.post('/api/product/account/register',json={'username':'taste-other','password':'Local-test-password-456'})
            other.post('/api/product/preferences/cabernet', json={'rating': 1})
        product = self.app.state.product
        captured = []
        def respond(payload):
            captured.append(payload)
            return {'text': 'Вам нравятся мягкие вина — это пока ориентир по анкете.', 'slugs': []}
        env = {'WINE_ASSISTANT_URL': 'https://example.com', 'WINE_ASSISTANT_KEY': 'test', 'WINE_ASSISTANT_MODEL': 'test'}
        with patch.dict('os.environ', env), patch.object(product, '_ai_complete', side_effect=respond):
            first = self.c.post('/api/product/assistant', json={'intent': 'taste_discussion', 'message': 'Расскажи о моём вкусе',
                'taste_profile': {'summary': 'CLIENT MUST NOT OVERRIDE'}, 'profile': 'someone-else'})
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()['mode'], 'ai')
            self.assertEqual(first.json()['recommendation_type'], 'taste')
            history = [{'role': 'user', 'content': 'Расскажи о моём вкусе'}, {'role': 'assistant', 'content': first.json()['text']}]
            self.c.post('/api/product/assistant', json={'intent': 'taste_discussion', 'message': 'А почему?', 'context': history})
        facts = json.loads(captured[0]['messages'][-1]['content'])
        self.assertEqual(len(facts['quiz_answers']), 6)
        self.assertEqual([(w['slug'], w['rating']) for w in facts['personal_wines']], [('merlo', 5)])
        self.assertNotIn('CLIENT MUST NOT OVERRIDE', json.dumps(facts))
        self.assertEqual(captured[1]['messages'][1:3], history)
        self.assertEqual(facts['candidates'], [])

    def test_unknown_ai_wine_is_rejected(self):
        self.c.post('/api/product/account/register',json={'username':'taste-owner','password':'Local-test-password-456'})
        product = self.app.state.product
        env = {'WINE_ASSISTANT_URL': 'https://example.com', 'WINE_ASSISTANT_KEY': 'test', 'WINE_ASSISTANT_MODEL': 'test'}
        with patch.dict('os.environ', env), patch.object(product, '_ai_complete', return_value={'text': 'Неверный подбор', 'slugs': ['invented']}):
            r = self.c.post('/api/product/assistant', json={'intent': 'taste_discussion', 'message': 'Посоветуй вино'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['mode'], 'catalog')
        self.assertTrue(r.json()['warning'])
        self.assertNotIn('invented', [w['slug'] for w in r.json()['items']])
        with TestClient(self.app) as guest:
            self.assertEqual(guest.post('/api/product/assistant', json={'intent': 'taste_discussion', 'message': 'Ещё?'}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
