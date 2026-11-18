import unittest


class FakeClient:
    def info(self):
        return {
            'MiniMaxH3SemanticBridgeConfigT8': {'input': {'required': {'model_name': [['t8_compat/real.safetensors']]}}},
            'LoraLoaderBypassModelOnly': {'input': {'required': {'lora_name': [['real.safetensors']]}}},
            'UNETLoader': {'input': {'required': {'unet_name': [['nested/model.safetensors']]}}},
        }

    def models(self, group):
        return {'semantic_bridge': ['t8_compat/real.safetensors'], 'loras': ['real.safetensors'],
                'diffusion_models': ['nested/model.safetensors']}.get(group, [])


class PreflightTests(unittest.TestCase):
    def check(self, cls, **inputs):
        from studio.preflight import check_workflow
        return check_workflow({'1': {'class_type': cls, 'inputs': inputs}}, None, FakeClient())

    def test_missing_bridge_is_reported(self):
        result = self.check('MiniMaxH3SemanticBridgeConfigT8', model_name='t8_compat/MISSING.safetensors')
        self.assertFalse(result.ready)
        self.assertTrue(any('MISSING' in e for e in result.errors))

    def test_missing_picture_lora_is_reported(self):
        self.assertFalse(self.check('LoraLoaderBypassModelOnly', lora_name='MISSING.safetensors').ready)

    def test_subfolder_is_not_ignored(self):
        self.assertFalse(self.check('UNETLoader', unet_name='model.safetensors').ready)
        self.assertTrue(self.check('UNETLoader', unet_name='nested/model.safetensors').ready)

    def test_required_parameter_and_unknown_node(self):
        self.assertFalse(self.check('UNETLoader').ready)
        self.assertFalse(self.check('UNKNOWN').ready)

