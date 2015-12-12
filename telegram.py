import os
import sys
import time
import imp
import logging
import json

import requests
from requests.exceptions import ConnectionError


class Bot(object):

    def __init__(self, settings):
        logging.basicConfig(stream=sys.stdout, level=logging.DEBUG if settings.debug else logging.INFO)
        self.token = settings.token
        self.update_id = 0
        self.settings = settings

        self.load()

    def load(self):
        """
        load
        Load all modules.
        See also: modprobe, rmmod, lsmod
        """

        self.commands = {
            # Usual text commands (e.g. "/echo 123")
            'user': {},
            'owner': {
                'load': self.load,
                'modprobe': self.modprobe,
                'rmmod': self.rmmod
            },
            # Modules for bot's reaction to a different message types
            'text': {},
            'photo': {},
            'audio': {},
            'video': {},
            'sticker': {}
        }

        for file in os.listdir('modules'):
            if file.endswith('.py') and file != '__init__.py':
                command_type, command = file.split('_', 1)
                self.modprobe(self, command[:-3])

    def modprobe(self, bot, *args):
        """
        modprobe <module>
        Load module.
        See also: load, rmmod, lsmod
        """

        if len(args) != 1:
            return

        found_module = None
        for command_type in self.commands:
            name = 'modules/{}_{}'.format(command_type, args[0])
            try:
                found_module = imp.find_module(name)
            except ImportError:
                continue
            else:
                break

        if found_module is None:
            error = 'MODULE: {} not found'.format(args[0])
            logging.error(error)
            return error

        try:
            method = imp.load_module(name, *found_module).main
        except Exception as e:
            error = "MODULE: Can't load {}".format(args[0])
            logging.error(error)
            logging.error(e)
            return error
        else:
            self.commands[command_type][args[0]] = method

        info = 'MODULE: {} loaded'.format(args[0])
        logging.info(info)
        return info

    def rmmod(self, bot, *args):
        """
        rmmod <module>
        Remove module.
        See also: load, modprobe, lsmod
        """

        if len(args) != 1:
            return

        if args[0] in ('load', 'modprobe', 'rmmod'):
            return "MODULE: can't remove {0}".format(args[0])

        found = False
        for command_type in self.commands:
            if args[0] in self.commands[command_type]:
                del self.commands[command_type][args[0]]
                found = True
                break

        if not found:
            return 'MODULE: {} not loaded'.format(args[0])

        info = 'MODULE: {} removed'.format(args[0])
        logging.info(info)
        return info

    def start(self):
        while True:
            updates = self.get_updates()
            logging.debug(updates)
            for update in updates:
                self.process(update)

    def process(self, update):
        """
        Process an update
        """

        self.chat_id = self._get_chat_id(update)

        # Process a text command
        if 'message' in update and 'text' in update['message'] and update['message']['text'].startswith('/') and 'forward_from' not in update['message']:
            text = update['message']['text'].lstrip('/')
            prefix = None
            error = False

            # Some user have been highlighted
            if ' > ' in text:
                text, prefix = text.split(' > ', 1)
                if not prefix.startswith('@') and prefix:
                    prefix = '@' + prefix
            if not text:
                return
            cmd, *args = text.split(' ')
            result = None

            if '@' in cmd:
                cmd, bot_name = cmd.split('@')
                if bot_name != self.settings.name:
                    return

            if cmd in self.commands['user'] or (cmd in self.commands['owner'] and self._is_owner(update)):
                command_type = 'user' if cmd in self.commands['user'] else 'owner'
                try:
                    cmd_main = self.commands[command_type][cmd]
                    if not getattr(cmd_main, 'prevent_pre_send', None):
                        self.pre_send()
                    result = cmd_main(self, *args)
                except TypeError as e:
                    if 'positional argument' in str(e):
                        result = 'wrong parameters'
                        error = True
                    if self.settings.debug:
                        raise
            elif cmd in self.commands['owner'] and not self._is_owner(update):
                result = '{}: access denied'.format(cmd)
                error = True
            else:
                error = True

            if result is not None:
                if prefix and not error:
                    result = '{}, {}'.format(prefix, result)
                self.send(self.chat_id, text=result)

        # Process a message with some media (audio, photo, video, etc)
        elif 'message' in update:
            for command_type in self.commands:
                obj = update['message'].get(command_type, None)
                if obj is not None:
                    [self.commands[command_type][cmd](self, obj, update) for cmd in self.commands[command_type]]

    def pre_send(self, chat_id=None, action='typing'):
        """
        Pre send hook. Send 'typing...' or another chat action
        """

        data = {
            'chat_id': chat_id if chat_id is not None else self.chat_id,
            'action': action
        }
        self.call('sendChatAction', 'POST', data=data)

    def send(self, chat_id=None, text=None, data={}):
        """
        Send message to a chat
        """

        if text is not None:
            data.update(chat_id=chat_id if chat_id is not None else self.chat_id, text=text)
            logging.debug('Sending: {}'.format(data))
            self.call('sendMessage', 'POST', data=data)

    def get_updates(self):
        """
        Get updates from telegram
        """

        try:
            updates = self.call('getUpdates', 'GET', params={
                                'timeout': getattr(self.settings, 'updates_timeout', 60), 'offset': self.update_id})
        except ConnectionError as e:
            logging.error(e)
        else:
            if updates:
                self.update_id = updates[-1]['update_id'] + 1
            return updates
        return []

    def call(self, method_name, http_method, **kwargs):
        """
        Call a Telegram API method
        """

        uri = 'https://api.telegram.org/bot{}/{}'.format(
            self.token, method_name)
        resp = requests.request(http_method, uri, **kwargs)
        try:
            content = json.loads(resp.content.decode('utf-8'))
        except ValueError:
            return []

        if content['ok']:
            return content['result']
        else:
            logging.error(resp.content)

    def _is_owner(self, update):
        return update.get('message', {}).get('from', {}).get('username', '') == self.settings.owner

    def _get_chat_id(self, update):
        return update.get('message', {}).get('chat', {}).get('id', None)
