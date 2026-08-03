import unittest

try:
    import numpy as np
    from agent.rknn_yolo import RknnYoloProvider
except ModuleNotFoundError:  # RKNN/Numpy are terminal-only dependencies.
    np = None
    RknnYoloProvider = None


@unittest.skipIf(np is None, "terminal NPU dependencies are not installed")
class RknnYoloPostProcessTests(unittest.TestCase):
    def setUp(self):
        self.provider = RknnYoloProvider.__new__(RknnYoloProvider)
        self.provider.input_width = 640
        self.provider.input_height = 640

    def test_model_zoo_six_output_layout_is_decoded_without_torch(self):
        outputs = []
        for size in (80, 40, 20):
            position = np.zeros((1, 64, size, size), dtype=np.float32)
            classes = np.zeros((1, 2, size, size), dtype=np.float32)
            outputs.extend((position, classes))
        outputs[1][0, 1, 40, 40] = 0.9

        boxes, classes, scores = self.provider._post_process(outputs, 0.25, 0.45)

        self.assertEqual(boxes.shape, (1, 4))
        self.assertEqual(classes.tolist(), [1])
        self.assertAlmostEqual(float(scores[0]), 0.9, places=5)

    def test_model_zoo_nine_output_layout_ignores_score_sum_tensor(self):
        outputs = []
        for size in (80, 40, 20):
            position = np.zeros((1, 64, size, size), dtype=np.float32)
            classes = np.zeros((1, 1, size, size), dtype=np.float32)
            score_sum = np.zeros((1, 1, size, size), dtype=np.float32)
            outputs.extend((position, classes, score_sum))
        outputs[1][0, 0, 20, 20] = 0.8

        boxes, classes, scores = self.provider._post_process(outputs, 0.25, 0.45)

        self.assertEqual(len(boxes), 1)
        self.assertEqual(classes.tolist(), [0])
        self.assertAlmostEqual(float(scores[0]), 0.8, places=5)

    def test_standard_ultralytics_flat_output_is_decoded(self):
        output = np.zeros((1, 6, 10), dtype=np.float32)
        output[0, :4, 0] = [320, 320, 100, 200]
        output[0, 4, 0] = 0.2
        output[0, 5, 0] = 0.9

        boxes, classes, scores = self.provider._post_process([output], 0.25, 0.45)

        self.assertEqual(boxes.round().astype(int).tolist(), [[270, 220, 370, 420]])
        self.assertEqual(classes.tolist(), [1])
        self.assertAlmostEqual(float(scores[0]), 0.9, places=5)

    def test_transposed_flat_output_is_decoded(self):
        output = np.zeros((1, 10, 6), dtype=np.float32)
        output[0, 0, :4] = [320, 320, 100, 200]
        output[0, 0, 5] = 0.8

        boxes, classes, scores = self.provider._post_process([output], 0.25, 0.45)

        self.assertEqual(len(boxes), 1)
        self.assertEqual(classes.tolist(), [1])
        self.assertAlmostEqual(float(scores[0]), 0.8, places=5)

    def test_nhwc_position_and_class_tensors_are_normalized(self):
        position = np.zeros((1, 80, 80, 64), dtype=np.float32)
        normalized_position = self.provider._position_nchw(position)
        classes = np.zeros((1, 80, 80, 3), dtype=np.float32)
        normalized_classes = self.provider._class_nchw(classes, (80, 80))

        self.assertEqual(normalized_position.shape, (1, 64, 80, 80))
        self.assertEqual(normalized_classes.shape, (1, 3, 80, 80))

    def test_restore_boxes_removes_letterbox_padding(self):
        boxes = np.array([[100.0, 160.0, 540.0, 480.0]], dtype=np.float32)
        restored = self.provider._restore_boxes(boxes, (360, 720), 640 / 720, (0.0, 160.0))

        self.assertEqual(restored[0].round().astype(int).tolist(), [112, 0, 608, 360])

    def test_runtime_input_adds_static_model_batch_dimension(self):
        image = np.zeros((640, 640, 3), dtype=np.uint8)
        runtime_input = self.provider._runtime_input(image)

        self.assertEqual(runtime_input.shape, (1, 640, 640, 3))
        self.assertTrue(runtime_input.flags.c_contiguous)


if __name__ == "__main__":
    unittest.main()
