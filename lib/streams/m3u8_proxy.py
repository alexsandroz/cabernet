
from urllib.parse import quote, unquote, urljoin
from datetime import datetime

import urllib3
from lib import m3u8
from lib.clients.web_handler import WebHTTPHandler
from lib.web.pages.templates import web_templates
from lib.streams.stream import Stream

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
                channel_uri = _channel_dict['json'].get('stream_url')
                if not channel_uri:
                    channel_uri = self.get_stream_uri(_channel_dict)
                if 'm3u8' not in channel_uri:
                    header = {'Location': channel_uri, 'Content-Type': 'video/MP2T'}
                    return {
                        'code': 302,
                        'headers': header,
                        'text': None}
                self.update_tuner_status('Starting')
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
            
            # Segments
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

            # keys
            elif _query_data and 'key' in _query_data:
                key_uri = unquote(_query_data['key'])
                response = plugin_obj.http_session.get(key_uri, headers=header, verify=False)
                if response.status_code >= 400:
                    msg = 'Error getting keys for channel:{}'.format(_channel_dict['uid'])
                    self.logger.error(msg)
                    return {
                        'code': 500,
                        'headers': {'Content-type': 'text/html'},
                        'text': msg}
                return {
                    'code': response.status_code,
                    'headers': {'Content-Type': response.headers['Content-Type'],
                                'Content-Length': str(len(response.content)),},
                    'content': response.content}

            # playlist
            elif _query_data and 'playlist' in _query_data:
                channel_uri = unquote(_query_data['playlist'])

            response = plugin_obj.http_session.get(channel_uri, headers=header, verify=False)
            
            if response.status_code >= 400:
                self.update_error_connection(_channel_dict)
                redirect = self.select_alternative_source(_channel_dict)
                if redirect is not None:
                    return redirect
                else:
                    msg = 'Error get m3u8 playlist for channel:{}'.format(_channel_dict['uid'])
                    self.logger.error(msg)
                    return {
                        'code': response.status_code,
                        'headers': {'Content-type': 'text/html'},
                        'text': msg } 

            WebHTTPHandler.rmg_station_scans[self.namespace][self.tuner_no]['channel_uri'] = response.request.url
            playlist = m3u8.loads(response.text)        

            # Rewrite segment URIs to not pass through the proxy
            for s in playlist.segments:
                uri = urljoin(response.url, s.uri)
                s.uri = f'{base_uri}?segment={quote(uri)}'
            for p in playlist.playlists: 
                uri = urljoin(response.url, p.uri)
                p.uri = f'{base_uri}?playlist={quote(uri)}'
            for k in playlist.keys: 
                if k and k.uri: 
                    uri = urljoin(response.url, k.uri)                
                    k.uri = f'{base_uri}?key={quote(uri)}' 
            
            playlist_data = playlist.dumps()
            kodi_prop = 'EXTM3U\n'
            kodi_prop += '#KODIPROP:inputstream=inputstream.ffmpegdirect\n'
            kodi_prop += '#KODIPROP:mimetype=application/x-mpegURL\n'
            kodi_prop += '#KODIPROP:inputstream.ffmpegdirect.manifest_type=hls\n'            
            kodi_prop += '#KODIPROP:inputstream.ffmpegdirect.stream_mode=timeshift\n'
            kodi_prop += '#KODIPROP:inputstream.ffmpegdirect.playback_as_live=true\n'
            kodi_prop += '#KODIPROP:inputstream.ffmpegdirect.is_realtime_stream=true\n'
            playlist_data.replace('EXTM3U\n', kodi_prop)
            response.headers['Content-Length'] = str(len(playlist_data))

            # Update channel connection status in the database
            _channel_dict.update({'last_seen': datetime.now().timestamp(), 'next_connection': None, 'error_count': 0})
            WebHTTPHandler.channels_db.update_connection_status(_channel_dict)

            return {
                'code': 200,
                'headers': response.headers,
                'text': playlist_data}
        except Exception as e:
            msg = 'Error generating M3U8 response for channel:{}. {}:{}'\
                    .format(_channel_dict['uid'], e, e.__traceback__.tb_lineno)
            self.logger.error(msg)       
            return { 
            'code': 500,
            'headers': {'Content-type': 'text/html'},
            'text': msg}

    def update_error_connection(self, _channel_dict):
        last_seen = _channel_dict.get('last_seen', None)
        if isinstance(last_seen, datetime):
            last_seen = last_seen.timestamp()
        error_count = _channel_dict.get('error_count', 0) + 1
        # Increment next_connection by 1 minute exponentially up to a limit of 1 day
        if error_count < 25:
            next_connection = datetime.now().timestamp() + (error_count * error_count * 60) 
        else:
            next_connection = datetime.now().timestamp() + (24 * 60 * 60)
        _channel_dict.update({'last_seen': last_seen, 'next_connection': next_connection, 'error_count': error_count})
        WebHTTPHandler.channels_db.update_connection_status(_channel_dict)

    def select_alternative_source(self, _channel_dict):
        content_uid = _channel_dict.get('content_uid')
        if not content_uid:
            return None

        self.logger.info('Source unavailable. Trying alternative source for channel:{}'.format(content_uid))
        alternative_sources = WebHTTPHandler.channels_db.get_channel_by_content_uid(content_uid)

        # Remove a fonte atual da lista de alternativas
        current_uid = _channel_dict['uid']
        alternative_sources = [src for src in alternative_sources if src['uid'] != current_uid]

        if not alternative_sources:
            self.logger.error(f'No alternative sources found for content_uid: {content_uid}')
            return None        

        redirect_uri = None
        for source in alternative_sources:
            plugin = self.plugins.plugins.get(source.get('namespace'))
            if not plugin or not plugin.plugin_obj or not plugin.plugin_obj.enabled:
                continue
            instance = plugin.plugin_obj.instances.get(source.get('instance'))
            if not instance and not instance.enabled:
                continue
            if source.get('enabled') == 1:
                redirect_uri = f"/{source.get('namespace')}/watch/{str(source['uid'])}"
                break

        if redirect_uri:
            header = {'Location': redirect_uri, 'Content-Type': 'application/vnd.apple.mpegurl'}
            return {
                'code': 302,
                'headers': header,
                'text': None}


    def update_tuner_status(self, _status):
        tuners = WebHTTPHandler.rmg_station_scans[self.namespace]
        tuner = tuners[self.tuner_no]
        if type(tuner) == dict and tuner['ch'] == self.channel_uid:
            if _status == 'Idle':
                tuners[self.tuner_no] = _status
            else:
                tuners[self.tuner_no]['status'] = _status
                tuners[self.tuner_no]['last_tune'] = datetime.now().timestamp()
                self.logger.debug('Update tuner:{} channel:{} status:{}'
                                 .format(self.tuner_no,self.channel_uid,_status))


    def clear_tuner_status(self):
        # If the tuner is not in use by 30 seconds, set it to Idle
        timeout = datetime.now().timestamp() - 30
        for _name_space, tuners in WebHTTPHandler.rmg_station_scans.items():
            for tuner_no, tuner in enumerate(tuners):
                if type(tuner) is dict:                
                    _instance = tuner['instance']
                    if tuner.get('last_tune', 0) < timeout:
                        self.logger.debug('Disconnect tuner:{} channel:{}'.format(tuner_no, tuner['ch']))
                        tuners[tuner_no] = 'Idle'
