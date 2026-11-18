import unittest


class ModelRegistryTests(unittest.TestCase):
    def test_profiles_route_by_task(self):
        from studio.model_registry import select_profile
        self.assertEqual(select_profile({'type': 'video', 'mode': 'text'}, {}), 'h3-fl2va-int8')
        self.assertEqual(select_profile({'type': 'video', 'mode': 'ref'}, {}), 'h3-ref2va-int8')
        self.assertEqual(select_profile({'type': 'image'}, {}), 'ph3-lora-int8')

    def test_picture_reuses_fl2va_and_downloads_are_deduplicated(self):
        from studio.model_registry import required_components
        jobs = [{'type': 'image'}, {'type': 'video', 'mode': 'text'}]
        components = required_components(jobs)
        names = [c['file'] for c in components]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(any('fl2va_pruned' in n for n in names))
        self.assertFalse(any('Combat' in n or 'Mystic' in n for n in names))

