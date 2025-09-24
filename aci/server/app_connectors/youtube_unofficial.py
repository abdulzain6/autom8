import requests
from typing import List, Dict, Any, Optional, Union
import yt_dlp
from pytube import YouTube as PyTubeYouTube
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from youtube_transcript_api.proxies import GenericProxyConfig
from youtube_comment_downloader import YoutubeCommentDownloader, SORT_BY_RECENT

from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import (
    NoAuthScheme,
    NoAuthSchemeCredentials,
)
from aci.server.app_connectors.base import AppConnectorBase
from aci.server.config import HTTP_PROXY

logger = get_logger(__name__)


class YoutubeUnofficial(AppConnectorBase):
    """
    YouTube Connector for AI agents using yt-dlp and other maintained libraries.
    Provides methods to search videos, get video details, transcripts, comments, and channel info.
    
    Uses yt-dlp for search and channel information, pytube for video details,
    youtube-transcript-api for transcripts, and youtube-comment-downloader for comments.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: str | None = None,
    ):
        super().__init__(linked_account, security_scheme, security_credentials, run_id=run_id)
        
        # Configure proxies
        self.proxies = None
        if HTTP_PROXY:
            self.proxies = {
                'http': HTTP_PROXY,
                'https': HTTP_PROXY
            }
            logger.info(f"YouTube connector configured with proxy: {HTTP_PROXY}")
        
        # Configure requests session with proxy
        self.session = requests.Session()
        if self.proxies:
            self.session.proxies.update(self.proxies)

    def _before_execute(self) -> None:
        """
        Setup before executing methods.
        """
        pass

    def search_videos(
        self,
        query: str,
        max_results: int = 10,
        order: str = "relevance",
        language: str = "en"
    ) -> Dict[str, Any]:
        """
        Search for YouTube videos using yt-dlp.

        Args:
            query: Search query
            max_results: Maximum number of results
            order: Sort order (relevance, date, rating, title, videoCount, viewCount)
            language: Language code

        Returns:
            Dict containing search results
        """
        logger.info(f"Searching YouTube videos for query: '{query}'")

        try:
            # Configure yt-dlp options
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'skip_download': True,
                'playlistend': max_results,
                'proxy': self.proxies.get('http') if self.proxies else None,
                'socket_timeout': 10,  # 10 second timeout
                'extractor_retries': 1,  # Minimal retries
            }

            # Map order parameter to yt-dlp sort options
            sort_map = {
                'relevance': None,  # default
                'date': 'upload_date',
                'rating': 'like_count',
                'viewCount': 'view_count',
                'title': None,  # not directly supported
            }

            if order in sort_map and sort_map[order]:
                ydl_opts['playlistreverse'] = True  # Reverse for descending order

            # Create yt-dlp instance
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                # Search for videos
                search_url = f"ytsearch{max_results}:{query}"
                info = ydl.extract_info(search_url, download=False)  # type: ignore

                videos = []
                if info and 'entries' in info and info['entries']:
                    for entry in info['entries']:  # type: ignore
                        if entry and isinstance(entry, dict):
                            video = {
                                'video_id': entry.get('id', ''),
                                'title': entry.get('title', ''),
                                'description': entry.get('description', ''),
                                'channel_title': entry.get('uploader', ''),
                                'channel_id': entry.get('uploader_id', ''),
                                'published_at': entry.get('upload_date'),
                                'duration': entry.get('duration'),
                                'view_count': entry.get('view_count'),
                                'thumbnail_url': entry.get('thumbnail'),
                                'url': entry.get('webpage_url', f"https://www.youtube.com/watch?v={entry.get('id', '')}")
                            }
                            videos.append(video)

                return {
                    'query': query,
                    'videos': videos,
                    'total_results': len(videos)
                }

        except Exception as e:
            logger.error(f"YouTube search error: {e}")
            raise Exception(f"Failed to search videos: {e}")

    def get_video_transcript(
        self,
        url: str,
        language: str = "en"
    ) -> Dict[str, Any]:
        """
        Get transcript for a YouTube video using youtube-transcript-api.

        Args:
            url: YouTube video URL
            language: Preferred language for the transcript

        Returns:
            Dict containing transcript text and metadata
        """
        logger.info(f"Getting transcript for video: {url}")

        # Extract video ID from URL
        if 'youtube.com/watch?v=' in url:
            video_id = url.split('v=')[1].split('&')[0]
        elif 'youtu.be/' in url:
            video_id = url.split('youtu.be/')[1].split('?')[0]
        else:
            video_id = url

        try:
            # Create proxy config for YouTubeTranscriptApi
            proxy_config = None
            if self.proxies:
                proxy_config = GenericProxyConfig(
                    http_url=self.proxies.get('http'),
                    https_url=self.proxies.get('https')
                )
            
            # Get transcript list with proxy support
            transcript_api = YouTubeTranscriptApi(proxy_config=proxy_config, http_client=self.session)
            transcript_list = transcript_api.list(video_id)
            
            # Try to get transcript in preferred language
            transcript = None
            try:
                transcript = transcript_list.find_transcript([language])
            except:
                # If preferred language not available, try English
                if language != 'en':
                    try:
                        transcript = transcript_list.find_transcript(['en'])
                    except:
                        pass
            
            # If no manual transcript, try auto-generated
            if transcript is None:
                try:
                    transcript = transcript_list.find_generated_transcript([language, 'en'])
                except:
                    raise Exception("No transcript available for this video")

            # Fetch the actual transcript data
            transcript_data = transcript.fetch()
            
            # Format as text
            formatter = TextFormatter()
            text = formatter.format_transcript(transcript_data)
            
            # Also return timed transcript
            timed_transcript = []
            for entry in transcript_data:
                try:
                    # Handle both dict and object types
                    if hasattr(entry, 'text') and hasattr(entry, 'start') and hasattr(entry, 'duration'):
                        timed_transcript.append({
                            'text': str(getattr(entry, 'text', '')),
                            'start': float(getattr(entry, 'start', 0)),
                            'duration': float(getattr(entry, 'duration', 0))
                        })
                    elif isinstance(entry, dict):
                        timed_transcript.append({
                            'text': str(entry.get('text', '')),
                            'start': float(entry.get('start', 0)),
                            'duration': float(entry.get('duration', 0))
                        })
                except (AttributeError, ValueError, TypeError, KeyError):
                    continue

            return {
                'video_id': video_id,
                'video_url': url,
                'language': str(transcript.language) if hasattr(transcript, 'language') else language,
                'is_generated': bool(transcript.is_generated) if hasattr(transcript, 'is_generated') else False,
                'text': text,
                'timed_transcript': timed_transcript,
                'transcript_length': len(timed_transcript)
            }
        except Exception as e:
            logger.error(f"Failed to get transcript for video {video_id}: {e}")
            raise Exception(f"Failed to get transcript: {e}")

    def get_channel_info(
        self,
        channel_url: str
    ) -> Dict[str, Any]:
        """
        Get information about a YouTube channel using yt-dlp.

        Args:
            channel_url: YouTube channel URL or channel ID

        Returns:
            Dict containing channel information
        """
        logger.info(f"Getting info for channel: {channel_url}")

        try:
            # Configure yt-dlp options for fast channel extraction
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,  # Don't extract individual videos
                'skip_download': True,
                'proxy': self.proxies.get('http') if self.proxies else None,
                'socket_timeout': 15,  # 15 second timeout
                'extractor_retries': 1,  # Minimal retries
                'playlistend': 1,  # Only get basic channel info, not all videos
                'lazy_playlist': True,  # Don't load all videos
            }

            # Create yt-dlp instance
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                # Extract channel information
                info = ydl.extract_info(channel_url, download=False)  # type: ignore

                if info and isinstance(info, dict):
                    return {
                        'channel_id': info.get('id', ''),
                        'title': info.get('title', ''),
                        'description': info.get('description', ''),
                        'subscriber_count': info.get('subscriber_count'),
                        'video_count': info.get('playlist_count'),
                        'thumbnail_url': info.get('thumbnail'),
                        'url': channel_url,
                        'verified': info.get('channel_is_verified', False),
                        'channel_follower_count': info.get('channel_follower_count'),
                        'uploader': info.get('uploader'),
                        'uploader_id': info.get('uploader_id')
                    }
                else:
                    return {
                        'channel_url': channel_url,
                        'error': 'Could not extract channel information',
                        'note': 'Channel information could not be retrieved'
                    }

        except Exception as e:
            logger.error(f"YouTube channel info error: {e}")
            return {
                'channel_url': channel_url,
                'error': str(e),
                'note': 'Failed to retrieve channel information'
            }
