
from urllib.parse import urlparse
import requests
from lib import m3u8
from lib.web.pages.templates import web_templates
from .stream import Stream


class M3U8Proxy(Stream):

    # There is no way to know the number of stream running on a redirect.
    # They can stop anytime without notification, so tuner tracking is
    # disabled

    def gen_m3u8_response(self, _channel_dict, _query_data):
        """
        Returns dict  where the dict is consistent with
        the method do_dict_response requires as an argument
        """
        channel_uri = self.get_stream_uri(_channel_dict)
        if not channel_uri:
            self.logger.warning('Unknown channel:{}'.format(_channel_dict['uid']))
            return {
                'code': 501,
                'headers': {'Content-type': 'text/html'},
                'text': web_templates['htmlError'].format('501 - Unknown channel')}

        self.logger.debug('Proxy M3U8 content to client')

        if _query_data and 'redirect' in _query_data:
            # return {
            #     'code': 302,
            #     'headers': {'Location': _query_data['redirect']},
            #     'text': None}
            # Perform a download instead of redirecting
            redirect_url = _query_data['redirect']
            self.logger.debug(f"Downloading content from: {redirect_url}")
            download_response = requests.get(redirect_url)
            download_response.raise_for_status()  # Raise an exception for bad status codes
            return {
                'code': 200,
                'headers': {'Content-type': download_response.headers.get('Content-Type', 'application/octet-stream')},
                'text': download_response.text
            }

        response = requests.get(channel_uri)
        response.raise_for_status()  # Raise an exception for bad status codes
        playlist = m3u8.loads(response.text)        

        parsed_url = urlparse(response.url)
        redirect_uri = f'{parsed_url.scheme}://{parsed_url.netloc}'
        #base_uri = f"/{_channel_dict['namespace']}/watch/{str(_channel_dict['uid'])}"

        # Rewrite segment URIs to not pass through the proxy
        for segment in playlist.segments:
        #    segment.uri = f"{base_uri}?redirect={redirect_uri}{segment.uri}"
            segment.uri = f"{redirect_uri}{segment.uri}"
            # self.logger.debug(f"segment.uri: {segment.uri}")

        playlist_data = playlist.dumps()
        return {
            'code': 200,
            'headers': {'Content-type': 'application/vnd.apple.mpegurl'},
            'text': playlist_data}
