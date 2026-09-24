import unittest
from unittest.mock import AsyncMock, patch

from flow_bridge.app import (
    ImageGenerationIn,
    _classify_error,
    _normalized_image_generation,
    flow_browser,
)
from flow_bridge.browser import FlowBrowserError


class CanonicalP0FlowRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_project_404_is_not_misclassified_as_session_expired(self):
        error = FlowBrowserError(
            "FLOW_PROJECT_NOT_FOUND: Flow project OLD redirected to "
            "https://flow.google.com/404?reason=project."
        )
        self.assertEqual(_classify_error(error), "FLOW_PROJECT_NOT_FOUND")

    async def test_image_generation_recovers_stale_flow_project(self):
        body = ImageGenerationIn(
            project_id="media-project",
            flow_project_id="OLD",
            asset_id="character:CHAR_001",
            idempotency_key="canonical-test-001",
            prompt="canonical character",
            model="Nano Banana 2",
            aspect_ratio="1:1",
            output_count=1,
        )
        caps = {
            "aspect_ratios": ["1:1", "16:9"],
            "models": ["Nano Banana 2"],
            "output_counts": [1],
        }
        with (
            patch.object(
                flow_browser,
                "image_capabilities",
                AsyncMock(side_effect=[
                    FlowBrowserError(
                        "FLOW_PROJECT_NOT_FOUND: Flow project OLD redirected to "
                        "https://flow.google.com/404?reason=project."
                    ),
                    caps,
                ]),
            ) as image_caps,
            patch.object(
                flow_browser,
                "restore_workspace",
                AsyncMock(return_value={"authenticated": True}),
            ) as restore,
            patch.object(
                flow_browser,
                "list_projects",
                AsyncMock(return_value=[{"id": "NEW", "href": "/project/NEW"}]),
            ) as projects,
        ):
            result = await _normalized_image_generation(body)

        self.assertEqual(result["project_id"], "NEW")
        self.assertTrue(result["project_recovered"])
        self.assertEqual(result["recovered_from_project_id"], "OLD")
        self.assertEqual(result["model"], "Nano Banana 2")
        self.assertEqual(result["aspect_ratio"], "1:1")
        self.assertEqual(image_caps.await_count, 2)
        restore.assert_awaited_once_with(None)
        projects.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
