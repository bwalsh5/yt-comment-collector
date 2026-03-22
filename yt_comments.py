"""
YouTube Comment Collector
-------------------------
This script collects comments from YouTube videos for specified channels/influencers.
It extracts comment text, date, creator name, and video title.

Requirements:
- Google API key (YouTube Data API v3)
- googleapiclient, pandas, and tqdm packages
"""

import os
import pandas as pd
from dotenv import load_dotenv
load_dotenv()
import datetime
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tqdm import tqdm

class YouTubeCommentCollector:
    def __init__(self, api_key):
        """Initialize the YouTube API client"""
        self.youtube = build('youtube', 'v3', developerKey=api_key)

    def get_channel_id(self, channel_username=None, channel_handle=None):
        """Get channel ID from username or handle"""
        if not channel_username and not channel_handle:
            raise ValueError("Either channel_username or channel_handle must be provided")

        try:
            if channel_username:
                request = self.youtube.channels().list(
                    part="id",
                    forUsername=channel_username
                )
                response = request.execute()

                if response['items']:
                    return response['items'][0]['id']

            # If we got here, either username lookup failed or we're using a handle
            if channel_handle:
                # Remove @ if present
                handle = channel_handle.replace('@', '')

                # Search for the channel using the search endpoint
                request = self.youtube.search().list(
                    part="snippet",
                    q=handle,
                    type="channel",
                    maxResults=5
                )
                response = request.execute()

                # Check if any results match our handle exactly (case insensitive)
                handle_lower = handle.lower()
                for item in response.get('items', []):
                    channel_title = item['snippet']['channelTitle'].lower()
                    if handle_lower in channel_title or channel_title in handle_lower:
                        return item['snippet']['channelId']

            return None
        except HttpError as e:
            print(f"An HTTP error occurred: {e}")
            return None

    def get_channel_videos(self, channel_id, max_results=50):
        """Get videos from a channel"""
        videos = []

        try:
            # Get uploads playlist ID
            request = self.youtube.channels().list(
                part="contentDetails",
                id=channel_id
            )
            response = request.execute()

            if not response['items']:
                return videos

            uploads_playlist_id = response['items'][0]['contentDetails']['relatedPlaylists']['uploads']

            # Get videos from uploads playlist
            next_page_token = None
            while True:
                request = self.youtube.playlistItems().list(
                    part="snippet",
                    playlistId=uploads_playlist_id,
                    maxResults=50,
                    pageToken=next_page_token
                )
                response = request.execute()

                for item in response['items']:
                    video_id = item['snippet']['resourceId']['videoId']
                    video_title = item['snippet']['title']
                    videos.append({
                        'video_id': video_id,
                        'title': video_title
                    })

                next_page_token = response.get('nextPageToken')

                if next_page_token is None or len(videos) >= max_results:
                    break

            return videos[:max_results]

        except HttpError as e:
            print(f"An HTTP error occurred: {e}")
            return videos

    def get_comments(self, video_id, max_comments=100):
        """Get comments for a video"""
        comments = []

        try:
            next_page_token = None
            while True:
                request = self.youtube.commentThreads().list(
                    part="snippet",
                    videoId=video_id,
                    maxResults=100,
                    pageToken=next_page_token,
                    textFormat="plainText"
                )
                response = request.execute()

                for item in response['items']:
                    comment = item['snippet']['topLevelComment']['snippet']
                    comments.append({
                        'text': comment['textDisplay'],
                        'author': comment['authorDisplayName'],
                        'date': comment['publishedAt'],
                        'likes': comment['likeCount']
                    })

                next_page_token = response.get('nextPageToken')

                if next_page_token is None or len(comments) >= max_comments:
                    break

            return comments[:max_comments]

        except HttpError as e:
            if e.resp.status == 403:
                # Comments might be disabled for this video
                print(f"Comments disabled or not accessible for video {video_id}")
            else:
                print(f"An HTTP error occurred: {e}")
            return comments

    def collect_comments(self, influencers, max_videos_per_channel=10, max_comments_per_video=100):
        """Collect comments from multiple influencers

        Parameters:
        influencers: List of dicts with 'name' and either 'username' or 'handle'
        max_videos_per_channel: Maximum number of videos to fetch per channel
        max_comments_per_video: Maximum number of comments to fetch per video

        Returns:
        DataFrame with comments
        """
        all_comments = []

        for influencer in influencers:
            print(f"\nProcessing influencer: {influencer['name']}")

            # Get channel ID
            channel_id = None
            if 'username' in influencer:
                channel_id = self.get_channel_id(channel_username=influencer['username'])
            elif 'handle' in influencer:
                channel_id = self.get_channel_id(channel_handle=influencer['handle'])

            if not channel_id:
                print(f"Could not find channel ID for {influencer['name']}")
                continue

            # Get videos
            videos = self.get_channel_videos(channel_id, max_results=max_videos_per_channel)
            print(f"Found {len(videos)} videos")

            # Get comments for each video
            for video in tqdm(videos, desc="Fetching comments"):
                video_comments = self.get_comments(video['video_id'], max_comments=max_comments_per_video)

                # Add additional info to each comment
                for comment in video_comments:
                    comment['influencer'] = influencer['name']
                    comment['video_id'] = video['video_id']
                    comment['video_title'] = video['title']

                all_comments.extend(video_comments)

        # Convert to DataFrame
        df = pd.DataFrame(all_comments)

        # Convert date strings to datetime objects
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])

        return df

def main():
    API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

    # Initialize collector
    collector = YouTubeCommentCollector(API_KEY)

    # Define influencers
    influencers = [
        {"name": "MKBHD", "handle": "MKBHD"},
        {"name": "Linus Tech Tips", "handle": "Linus Tech Tips"}
        # Add more influencers as needed
    ]

    # Collect comments
    comments_df = collector.collect_comments(
        influencers=influencers,
        max_videos_per_channel=5,  # Adjust as needed
        max_comments_per_video=50  # Adjust as needed
    )

    # Save to CSV
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"youtube_comments_{timestamp}.csv"
    comments_df.to_csv(output_file, index=False)

    print(f"\nData collection complete! Saved to {output_file}")
    print(f"Total comments collected: {len(comments_df)}")

if __name__ == "__main__":
    main()