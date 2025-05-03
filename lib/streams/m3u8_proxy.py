
from urllib.parse import quote, unquote, urljoin, urlparse
import requests
from datetime import datetime
from lib import m3u8
from lib.clients.web_handler import WebHTTPHandler
from lib.web.pages.templates import web_templates
from .stream import Stream


class M3U8Proxy(Stream):

    def __init__(self, _plugins, _hdhr_queue):
        super().__init__(_plugins, _hdhr_queue)
        self.ch_num = ''
        self.tuner_no = -1
                    
    def gen_m3u8_response(self, _channel_dict, _query_data):
        """
        Returns dict  where the dict is consistent with
        the method do_dict_response requires as an argument
        """
        channel_uri = _channel_dict['json']['stream_url']
        if not channel_uri:
            self.logger.warning('Unknown channel:{}'.format(_channel_dict['uid']))
            return {
                'code': 501,
                'headers': {'Content-type': 'text/html'},
                'text': web_templates['htmlError'].format('501 - Unknown channel')}

        self.namespace = _channel_dict['namespace']
        self.instance = _channel_dict['instance']
        self.ch_num = _channel_dict['uid']
        base_path = f"/{self.namespace}/watch/{str(_channel_dict['uid'])}"
        base_uri = f"http://{self.config['web']['plex_accessible_ip']}:{self.config['web']['plex_accessible_port']}{base_path}"
        self.tuner_no = self.find_tuner(self.namespace, self.instance, self.ch_num, False, reuse=True)
        
        if _query_data and 'segment' in _query_data:
            server_uri = WebHTTPHandler.rmg_station_scans[self.namespace][self.tuner_no]['server_uri']
            segment_uri = urljoin(server_uri, unquote(_query_data['segment']))
            self.update_tuner_status('Streaming')
            return {
                'code': 302,
                'headers': {'Location': segment_uri},
                'text': None}
        elif _query_data and 'playlist' in _query_data:
            server_uri = WebHTTPHandler.rmg_station_scans[self.namespace][self.tuner_no]['server_uri']
            channel_uri = urljoin(server_uri, unquote(_query_data['playlist']))
            self.update_tuner_status('Streaming')

        response = requests.get(channel_uri)
        if response.status_code >= 400:
            self.logger.error('Error tunning channel:{}'.format(_channel_dict['uid']))
            return {
                'code': response.status_code,
                'headers': {'Content-type': 'text/html'},
                'text': response.text}

        response.raise_for_status()  # Raise an exception for bad status codes
        if not 'server_uri' in WebHTTPHandler.rmg_station_scans[self.namespace][self.tuner_no]:
            WebHTTPHandler.rmg_station_scans[self.namespace][self.tuner_no]['server_uri'] = response.url
        playlist = m3u8.loads(response.text)        

        # Rewrite segment URIs to not pass through the proxy
        for s in playlist.segments: 
            s.uri = f'{base_uri}?segment={quote(s.uri)}'
        for p in playlist.playlists: 
            p.uri = f'{base_uri}?playlist={quote(p.uri)}'
           
        playlist_data = playlist.dumps()
        response.headers['Content-Length'] = str(len(playlist_data))
        return {
            'code': 200,
            'headers': response.headers,
            'text': playlist_data}


    def update_tuner_status(self, _status):
        scan_list = WebHTTPHandler.rmg_station_scans[self.namespace]
        tuner = scan_list[self.tuner_no]
        if type(tuner) == dict and tuner['ch'] == self.ch_num:
            WebHTTPHandler.rmg_station_scans[self.namespace][self.tuner_no]['status'] = _status
