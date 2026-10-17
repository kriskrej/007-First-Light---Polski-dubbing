import struct
import unittest

from dub007.dlge import parse


class ContainerRootTests(unittest.TestCase):
    references = [{'hash': '0000000000000001'}, {'hash': '0000000000000002'}]

    def payload(self, kinds, root_kind, ordinal):
        return (struct.pack('<II', 0, 1)
                + b''.join(bytes([kind]) + struct.pack('<III', 0, 0, 0) for kind in kinds)
                + struct.pack('<H', (root_kind << 12) | ordinal))

    def test_type_four_containers_advance_a_later_type_two_root(self):
        raw = self.payload([4, 4, 4, 2], 2, 3)
        parsed = parse(raw, self.references)
        self.assertEqual(parsed['root_typed_index'], 0x2003)
        self.assertEqual(len(parsed['containers']), 4)
        self.assertEqual(parsed['parsed_bytes'], len(raw))

    def test_type_four_root_uses_shared_container_ordinal(self):
        parsed = parse(self.payload([2, 3, 2, 4], 4, 3), self.references)
        self.assertEqual(parsed['root_typed_index'], 0x4003)

    def test_matching_local_index_does_not_authorize_wrong_global_type(self):
        # There is a type 2 at its local index zero, but global zero is type 4.
        with self.assertRaisesRegex(ValueError, 'Invalid global root'):
            parse(self.payload([4, 2], 2, 0), self.references)

    def test_root_outside_shared_container_table_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Invalid global root'):
            parse(self.payload([2, 4], 4, 2), self.references)
