import datetime
import logging
import os
import re
import subprocess
import sys
import argparse
import time
from typing import List, TypedDict, Union

import requests

TWITCH_API_CLIENT_ID = "xxxxxxxxxxxxxxxxx"
TWITCH_API_CLIENT_SECRET = "oooooooooooooooooooooo"
FILE_NAME_FORMAT = "[{user_login}]{stream_started}_{escaped_title}.ts"
TIME_FORMAT = "%y%m%d_%H%M%S"

logger = logging.getLogger()
logger.setLevel(logging.INFO)
fmt = logging.Formatter("{asctime} {levelname} {name} {message}", style="{")
stream_hdlr = logging.StreamHandler()
stream_hdlr.setFormatter(fmt)
logger.addHandler(hdlr=stream_hdlr)


def escape_filename(s: str) -> str:
    """Remove special charactors that cannot use for filen path"""
    return re.sub(r"[/\\?%*:|\"<>.\n]", "", s)


class StreamData(TypedDict):
    id: str
    user_id: str
    user_login: str
    game_id: str
    game_name: str
    type: str
    title: str
    viewer_count: int
    started_at: str
    language: str
    thumbnail_url: str
    tag_ids: List[str]
    is_mature: bool


class TwitchRecorder:
    def __init__(self, username: str, quality: str) -> None:
        logger.info("Twitch Recorder initializing start!")

        self.client_id = TWITCH_API_CLIENT_ID
        self.oauth_token = ""
        self._oauth_token_expires = 0

        self.ffmpeg_path = "ffmpeg"
        self.refresh = 5.0
        self.root_path = "./"

        self.username = username
        self.quality = quality

        self.file_dir = os.path.join(self.root_path, self.username)

        if not self.check_streamlink():
            sys.exit(1)
        if not self.get_oauth_token():
            sys.exit(1)
        if not self.check_user_exist():
            sys.exit(1)

    def get_oauth_token(self) -> bool:
        """Get oauth token from twitch api server using client id"""
        logger.info("Request oauth token from Twitch API server...")
        try:
            data = {
                "client_id": TWITCH_API_CLIENT_ID,
                "client_secret": TWITCH_API_CLIENT_SECRET,
                "grant_type": "client_credentials",
                "scope": ""
            }
            resp = requests.post("https://id.twitch.tv/oauth2/token", data=data)
            if resp.status_code != 200:
                return False
            resp_json = resp.json()
            access_token: str = resp_json["access_token"]
            token_type: str = resp_json["token_type"]
            self.oauth_token = f"{token_type.title()} {access_token}"
            self._oauth_token_expires = time.time() + resp_json["expires_in"]
            logger.debug("oauth_token is %s, expires at %d", self.oauth_token, self._oauth_token_expires)
        except requests.RequestException as e:
            logger.error("Fail to get oAuth token: %s", e)
            return False
        else:
            return True

    def check_streamlink(self) -> bool:
        """check streamlink >= 2.0.0 is installed"""
        try:
            ret = subprocess.check_output(["streamlink", "--version"], universal_newlines=True)
            re_ver = re.search(r"streamlink (\d+)\.(\d+)\.(\d+)", ret, flags=re.IGNORECASE)
            if not re_ver:
                return False
            s_ver = tuple(map(int, re_ver.groups()))
            return s_ver[0] >= 2
        except FileNotFoundError:
            logger.error("Cannot find streamlink! Install streamlink first.")
            return False

    def check_oauth_token(self) -> None:
        """Auto re-request oauth token before expire"""
        if time.time() + 3600 > self._oauth_token_expires:
            self.get_oauth_token()

    def check_user_exist(self) -> bool:
        """Check username is vaild (https://dev.twitch.tv/docs/api/reference#get-users)"""
        logger.info("Checking user exists...")
        try:
            header = {
                "Client-ID": self.client_id,
                "Authorization": self.oauth_token
            }
            resp = requests.get(f"https://api.twitch.tv/helix/users?login={self.username}", headers=header)
            if resp.status_code != 200:
                logger.error("HTTP ERROR: %s", resp.status_code)
                logger.debug(resp.text)
                return False
            if not resp.json().get("data"):
                logger.error("Response is empty!")
                return False
        except requests.RequestException as e:
            logger.error("Fail to check user: %s", e)
            return False
        else:
            return True

    def check_streaming(self) -> Union[StreamData, None]:
        """Get stream info (https://dev.twitch.tv/docs/api/reference#get-streams)"""
        try:
            header = {
                "Client-ID": self.client_id,
                "Authorization": self.oauth_token
            }
            resp = requests.get(f"https://api.twitch.tv/helix/streams?user_login={self.username}", headers=header,
                                timeout=15)
            if resp.status_code != 200:
                logger.error("HTTP ERROR: %s", resp.status_code)
                return
            data = resp.json().get("data", [])
            if not data:
                logger.error("Search result is empty!")
                return
            return data[0]
        except requests.RequestException as e:
            logger.error("Fail to get stream info: %s", e)
            return

    def loop(self):
        """main loop function"""
        logger.info("Loop start!")
        while True:
            stream_data = self.check_streaming()
            if stream_data is None:
                logger.info("%s is currently offline, checking again in %.1f seconds.", self.username, self.refresh)
                time.sleep(self.refresh)
            else:
                logger.info("%s online. Stream recording in session.", self.username)
                _data = {
                    "escaped_title": escape_filename(stream_data["title"]),
                    "stream_started": datetime.datetime.fromisoformat(
                        stream_data["started_at"].replace("Z", "+00:00")).astimezone().strftime(TIME_FORMAT),
                    "record_started": datetime.datetime.now().strftime(TIME_FORMAT)
                }
                file_name = FILE_NAME_FORMAT.format(**stream_data, **_data)
                file_path = os.path.join(self.file_dir, file_name)

                uq_num = 0
                while os.path.exists(file_path):
                    logger.warning("File already exists, will add numbers: %s", file_path)
                    uq_num += 1
                    file_path_no_ext, file_ext = os.path.splitext(file_path)
                    if uq_num > 1 and file_path_no_ext.endswith(f" ({uq_num - 1})"):
                        file_path_no_ext = file_path_no_ext.removesuffix(f" ({uq_num - 1})")
                    file_path = f"{file_path_no_ext} ({uq_num}){file_ext}"

                # start streamlink process
                logger.info("Straming video will save at %s", file_path)
                ret = subprocess.call(
                    ["streamlink", "--twitch-disable-hosting", "--twitch-disable-ads", "twitch.tv/" + self.username,
                     self.quality, "-o", file_path])

                if ret != 0:
                    logger.warning("Unexpected error. will try again in 30 seconds.")
                    time.sleep(30)

                # end streamlink process
                logger.info("Recording stream is done. Going back to checking...")
                time.sleep(self.refresh)

    def run(self):
        """run"""
        if self.refresh < 5:
            print("Check interval should not be lower than 5 seconds.")
            self.refresh = 5
            print("System set check interval to 5 seconds.")
        # create directory for recordedPath and processedPath if not exist
        if not os.path.isdir(self.file_dir):
            os.makedirs(self.file_dir)
        self.loop()


def main():
    parser = argparse.ArgumentParser(description="Simple Twitch recording script")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-q", "--quality", default="best")
    parser.add_argument("-d", "--debug", action="store_true")
    # parser.add_argument("--logging-telegram", action="store_true")
    args = parser.parse_args()
    print("必輸參數:" + args.__str__())

    if args.debug:
        logger.setLevel(logging.DEBUG)
    recorder = TwitchRecorder(args.username, args.quality)
    recorder.run()


if __name__ == "__main__":
    main()
