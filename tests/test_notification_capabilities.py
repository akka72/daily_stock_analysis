# -*- coding: utf-8 -*-
"""Tests for notification channel rendering capability profiles."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.notification import NotificationChannel
from src.notification_capabilities import (
    CHANNEL_PROFILES,
    PreparedMessage,
    all_channel_profiles,
    get_channel_profile,
    normalize_channel_name,
)


class NotificationCapabilityProfileTestCase(unittest.TestCase):
    def test_profiles_cover_all_notification_channels(self):
        profile_channels = {profile.channel for profile in all_channel_profiles()}
        expected = {channel.value for channel in NotificationChannel}

        self.assertTrue(expected.issubset(profile_channels))

    def test_get_channel_profile_accepts_enum_or_string(self):
        self.assertEqual(get_channel_profile(NotificationChannel.FEISHU).channel, "feishu")
        self.assertEqual(get_channel_profile("SLACK").markdown, "mrkdwn")
        self.assertEqual(get_channel_profile("missing").channel, "unknown")

    def test_core_channels_keep_full_report_defaults(self):
        self.assertEqual(CHANNEL_PROFILES["feishu"].default_mode, "full_report")
        self.assertEqual(CHANNEL_PROFILES["telegram"].default_mode, "full_report")
        self.assertEqual(CHANNEL_PROFILES["slack"].default_mode, "full_report")
        self.assertEqual(CHANNEL_PROFILES["wechat"].default_mode, "full_report")
        self.assertEqual(CHANNEL_PROFILES["email"].default_mode, "full_html")

    def test_prepared_message_keeps_legacy_text_fallback(self):
        prepared = PreparedMessage(
            channel="feishu",
            text="raw report",
            formatted_text="formatted report",
            fallback_text="fallback report",
        )

        self.assertEqual(prepared.content_for_text_send, "formatted report")

        fallback = PreparedMessage(channel="custom", text="raw report", fallback_text="fallback report")
        self.assertEqual(fallback.content_for_text_send, "fallback report")

    def test_normalize_channel_name_handles_empty_values(self):
        self.assertEqual(normalize_channel_name(None), "unknown")
        self.assertEqual(normalize_channel_name("  Telegram  "), "telegram")


if __name__ == "__main__":
    unittest.main()
