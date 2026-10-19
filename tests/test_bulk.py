import threading
import unittest

from dub007.bulk import pipeline


class BulkPipelineTests(unittest.TestCase):
    def test_request_overlaps_fitting_without_concurrent_posts_or_duplicate_hashes(self):
        fitting = threading.Event()
        next_started = threading.Event()
        calls = []
        def produce(h):
            calls.append(h)
            if h == 'second':
                next_started.set()
                self.assertTrue(fitting.wait(2))
            return h
        def consume(h):
            if h == 'first':
                fitting.set()
                self.assertTrue(next_started.wait(2))
            return h
        self.assertEqual(list(pipeline(['first', 'second', 'first'], produce=produce, consume=consume)), ['first', 'second'])
        self.assertEqual(calls, ['first', 'second'])

    def test_fitting_failure_drains_only_the_already_prefetched_request(self):
        calls = []
        def produce(h):
            calls.append(h)
            return h
        def consume(h):
            raise RuntimeError('encoder unavailable; preserve generated take')
        with self.assertRaisesRegex(RuntimeError, 'encoder unavailable'):
            list(pipeline(['first', 'second', 'third'], produce=produce, consume=consume))
        self.assertEqual(calls, ['first', 'second'])

    def test_uncertain_paid_result_stops_without_retry_or_next_request(self):
        calls = []
        def produce(h):
            calls.append(h)
            raise RuntimeError('uncertain paid result')
        with self.assertRaisesRegex(RuntimeError, 'uncertain'):
            list(pipeline(['first', 'second'], produce=produce, consume=lambda r:r))
        self.assertEqual(calls, ['first'])

    def test_timing_hold_is_a_result_and_never_automatically_retried(self):
        calls = []
        def produce(h):
            calls.append(h)
            return h
        results = list(pipeline(['a','b'], produce=produce, consume=lambda h:{'hash':h,'status':'needs_agent_adaptation'}))
        self.assertEqual(calls, ['a','b'])
        self.assertEqual(len(results), 2)

    def test_empty_queue_has_no_side_effect(self):
        self.assertEqual(list(pipeline([], produce=lambda h:self.fail('must not generate'))), [])
