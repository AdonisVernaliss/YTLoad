import unittest
from unittest.mock import patch

import boto3
from botocore.httpsession import URLLib3Session

from ytloadlib.r2_delivery import Boto3MultipartStore, R2DeliveryConfig


class HostedR2DependencySmokeTests(unittest.TestCase):
    def test_production_configuration_constructs_the_optional_client_offline(self):
        endpoint = 'https://0123456789abcdef.r2.cloudflarestorage.com'
        config = R2DeliveryConfig.from_environ({
            'YTLOAD_R2_ENDPOINT_URL': endpoint,
            'YTLOAD_R2_BUCKET': 'ytload-media',
            'YTLOAD_R2_ACCESS_KEY_ID': 'test-access-key',
            'YTLOAD_R2_SECRET_ACCESS_KEY': 'test-secret-key',
            'YTLOAD_MEDIA_CONTROL_URL': 'https://downloads.example.com/ytload/media-control',
            'YTLOAD_MEDIA_CONTROL_SECRET': 'test-control-secret-12345678901234567890',
        })
        with patch.object(URLLib3Session, 'send', side_effect=AssertionError('Network access is not allowed.')):
            store = Boto3MultipartStore(config)
        self.assertIsNotNone(boto3.__version__)
        self.assertEqual(store.bucket, 'ytload-media')
        self.assertEqual(store.client.meta.endpoint_url, endpoint)
        self.assertEqual(store.client.meta.region_name, 'auto')
        self.assertEqual(store.client.meta.config.signature_version, 's3v4')


if __name__ == '__main__':
    unittest.main()
