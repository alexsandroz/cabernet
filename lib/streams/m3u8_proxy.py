
from urllib.parse import quote, unquote, urljoin
from datetime import datetime

import urllib3
from lib import m3u8
from lib.clients.web_handler import WebHTTPHandler
from lib.web.pages.templates import web_templates
from .stream import Stream

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class M3U8Proxy(Stream):

    def __init__(self, _plugins, _hdhr_queue):
        super().__init__(_plugins, _hdhr_queue)
        self.channel_uid = ''
        self.tuner_no = -1
                    
    def gen_m3u8_response(self, _channel_dict, _query_data):
        """
        Returns dict  where the dict is consistent with
        the method do_dict_response requires as an argument
        """
        try:
            self.clear_tuner_status()
            self.namespace = _channel_dict['namespace']
            self.instance = _channel_dict['instance']
            self.channel_uid = _channel_dict['uid']
            self.tuner_no = self.find_tuner(self.namespace, self.instance, self.channel_uid, False, reuse=True)

            if not 'channel_uri' in WebHTTPHandler.rmg_station_scans[self.namespace][self.tuner_no]:
                self.update_tuner_status('Starting')
                channel_uri = self.get_stream_uri(_channel_dict)
                WebHTTPHandler.rmg_station_scans[self.namespace][self.tuner_no]['channel_uri'] = channel_uri
            else:
                channel_uri = WebHTTPHandler.rmg_station_scans[self.namespace][self.tuner_no]['channel_uri']

            if not channel_uri:
                self.logger.error('Unknown channel:{}'.format(_channel_dict['uid']))
                self.update_tuner_status('Idle')
                return {
                    'code': 501,
                    'headers': {'Content-type': 'text/html'},
                    'text': web_templates['htmlError'].format('501 - Unknown channel')}

            base_path = f"/{self.namespace}/watch/{str(_channel_dict['uid'])}"
            base_uri = f"http://{self.config['web']['plex_accessible_ip']}:{self.config['web']['plex_accessible_port']}{base_path}"
            
            header = _channel_dict['json'].get('Header')            
            plugin_obj = self.plugins.plugins[_channel_dict['namespace']].plugin_obj
            if _query_data and 'segment' in _query_data:
                segment_uri = unquote(_query_data['segment'])
                header = {'Location': segment_uri}
                if _channel_dict['json'].get('Header'):
                    header.update(_channel_dict['json'].get('Header'))     
                self.update_tuner_status('Streaming')
                return {
                    'code': 302,
                    'headers': header,
                    'text': None}
            elif _query_data and 'key' in _query_data:
                key_uri = unquote(_query_data['key'])
                response = plugin_obj.http_session.get(key_uri, headers=header, verify=False)
                if response.status_code >= 400:
                    self.logger.error('Error tunning channel:{}'.format(_channel_dict['uid']))
                    self.update_tuner_status('Idle')
                return {
                    'code': response.status_code,
                    'headers': {'Content-Type': response.headers['Content-Type'],
                                'Content-Length': str(len(response.content)),},
                    'content': response.content}
            elif _query_data and 'playlist' in _query_data:
                channel_uri = unquote(_query_data['playlist'])

            response = plugin_obj.http_session.get(channel_uri, headers=header, verify=False)
            if response.status_code >= 400:
                self.logger.error('Error tunning channel:{}'.format(_channel_dict['uid']))
                self.update_tuner_status('Idle')
                return {
                    'code': response.status_code,
                    'headers': {'Content-type': 'text/html'},
                    'text': response.text}        

            playlist = m3u8.loads(response.text)        

            # Rewrite segment URIs to not pass through the proxy
            for s in playlist.segments:
                uri = urljoin(response.url, s.uri)
                s.uri = f'{base_uri}?segment={quote(uri)}'
            for p in playlist.playlists: 
                uri = urljoin(response.url, s.uri)
                p.uri = f'{base_uri}?playlist={quote(uri)}'
            for k in playlist.keys: 
                if k and k.uri: 
                    uri = urljoin(response.url, s.uri)                
                    k.uri = f'{base_uri}?key={quote(uri)}' 
            
            playlist_data = playlist.dumps()
            response.headers['Content-Length'] = str(len(playlist_data))
            return {
                'code': 200,
                'headers': response.headers,
                'text': playlist_data}
        except Exception as e:
            self.logger.error(f"An error occurred while generating the M3U8 response: {e}")
            self.update_tuner_status('Idle')
            return {
            'code': 500,
            'headers': {'Content-type': 'text/html'},
            'text': web_templates['htmlError'].format('500 - Internal Server Error')}



    def update_tuner_status(self, _status):
        tuners = WebHTTPHandler.rmg_station_scans[self.namespace]
        tuner = tuners[self.tuner_no]
        if type(tuner) == dict and tuner['ch'] == self.channel_uid:
            if _status == 'Idle':
                tuners[self.tuner_no] = _status
            else:
                tuners[self.tuner_no]['status'] = _status
                tuners[self.tuner_no]['last_tune'] = datetime.now().timestamp()

    def clear_tuner_status(self):
        # If the tuner is not in use by 15 seconds, set it to Idle
        timeout = datetime.now().timestamp() - 15
        for _name_space, tuners in WebHTTPHandler.rmg_station_scans.items():
            for tuner_no, tuner in enumerate(tuners):
                if type(tuner) is dict:                
                    _instance = tuner['instance']
                    if tuner.get('last_tune', 0) < timeout:
                        self.logger.info('Disconnect tuner:{} channel:{}'.format(tuner_no, tuner['ch']))
                        tuners[tuner_no] = 'Idle'
