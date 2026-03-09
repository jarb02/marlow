"""Marlow OS bridges — interaction channels.

All user interaction (voice, sidebar, Telegram, console) goes through
bridges that implement BridgeBase. Each bridge receives input, sends
it to the kernel as a goal, and routes the response back.
"""
