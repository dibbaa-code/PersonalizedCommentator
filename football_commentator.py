import asyncio
import logging
import os
import random
from pathlib import Path

import av
import numpy as np
from dotenv import load_dotenv
from google.genai.types import Blob
from vision_agents.core import Agent, Runner, User
from vision_agents.core.agents import AgentLauncher
from vision_agents.core.edge.events import TrackAddedEvent
from vision_agents.plugins import getstream, gemini

logger = logging.getLogger(__name__)

load_dotenv()

INSTRUCTIONS_PATH = os.path.join(os.path.dirname(__file__), "instructions.md")


def get_instructions() -> str:
    """Read instructions.md and replace placeholders with env values."""
    with open(INSTRUCTIONS_PATH, "r") as f:
        template = f.read()

    fav_team = os.getenv("FAV_TEAM_NAME", "")
    level = os.getenv("KNOWLEDGE_LEVEL", "beginner")
    style = os.getenv("COMMENTARY_STYLE", "roasting")
    team1 = os.getenv("TEAM1_NAME", "Green Bay Packers")
    team2 = os.getenv("TEAM2_NAME", "Chicago Bears")
    team1_color = os.getenv("TEAM1_COLOR", "yellow")
    team2_color = os.getenv("TEAM2_COLOR", "navy blue with orange")

    instructions = template.replace("{FAV_TEAM_NAME}", fav_team if fav_team else "not specified")
    instructions = instructions.replace("{KNOWLEDGE_LEVEL}", level.capitalize())
    instructions = instructions.replace("{COMMENTARY_STYLE}", style.capitalize())
    instructions = instructions.replace("{TEAM1_NAME}", team1)
    instructions = instructions.replace("{TEAM2_NAME}", team2)
    instructions = instructions.replace("{TEAM1_COLOR}", team1_color)
    instructions = instructions.replace("{TEAM2_COLOR}", team2_color)

    return instructions


class AudioStreamer:
    """Streams audio from a video file to Gemini (video handled separately by framework)."""

    def __init__(self, path: str):
        self.path = Path(path)
        self._stopped = False

    async def stream_to_gemini(self, agent):
        """Stream audio to Gemini Live."""
        container = av.open(str(self.path))

        audio_stream = container.streams.audio[0] if container.streams.audio else None

        if not audio_stream:
            logger.warning("No audio stream in video file")
            container.close()
            return

        # Resample to 16kHz mono PCM (Gemini's expected format)
        audio_resampler = av.audio.resampler.AudioResampler(
            format='s16', layout='mono', rate=16000
        )

        logger.info("Streaming audio to Gemini...")

        try:
            while not self._stopped:
                try:
                    for packet in container.demux(audio_stream):
                        if self._stopped:
                            break

                        for frame in packet.decode():
                            if self._stopped:
                                break

                            for resampled in audio_resampler.resample(frame):
                                audio_bytes = resampled.to_ndarray().flatten().astype(np.int16).tobytes()

                                if agent.llm and agent.llm.connected:
                                    blob = Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                                    await agent.llm._session.send_realtime_input(audio=blob)

                        await asyncio.sleep(0.001)

                    # Loop audio with video
                    container.seek(0)
                    logger.info("Audio looped")

                except Exception as e:
                    logger.error(f"Audio stream error: {e}")
                    await asyncio.sleep(1)

        finally:
            container.close()
            logger.info("Audio streaming stopped")

    def stop(self):
        self._stopped = True


async def create_agent(**kwargs) -> Agent:
    llm = gemini.Realtime()
    instructions = get_instructions()

    # Get video path for publishing
    video_path = os.getenv("VIDEO_PATH", "")

    agent = Agent(
        edge=getstream.Edge(),
        agent_user=User(name="AI Sports Commentator", id="agent"),
        instructions=instructions,
        processors=[],
        llm=llm,
    )

    # Set video track override so the video is displayed in the call
    if video_path:
        agent.set_video_track_override_path(video_path)

    style = os.getenv("COMMENTARY_STYLE", "enthusiastic")
    level = os.getenv("KNOWLEDGE_LEVEL", "beginner")

    level_reminder = {
        "beginner": "Explain terms simply.",
        "intermediate": "Explain tactics.",
        "expert": "Use jargon freely.",
    }.get(level, "")

    if style == "roasting":
        questions = [
            f"What's happening? Roast it. 1-2 sentences. {level_reminder}",
            f"Comment on that play with roasts. 1-2 sentences. {level_reminder}",
        ]
    else:
        questions = [
            f"What's happening? 1-2 sentences. {level_reminder}",
            f"Comment on that play. 1-2 sentences. {level_reminder}",
        ]

    team1 = os.getenv("TEAM1_NAME", "Green Bay Packers")
    team2 = os.getenv("TEAM2_NAME", "Chicago Bears")
    opening_prompt = f"Welcome to {team1} vs {team2}! Quick intro in 1-2 sentences."

    # Audio streamer sends video's audio to Gemini (video handled by framework)
    streamer = AudioStreamer(video_path) if video_path else None

    commentary_task = None
    video_task = None

    async def run_commentary():
        """Periodic commentary loop."""
        await asyncio.sleep(3)

        await agent.simple_response(opening_prompt)
        await asyncio.sleep(5)

        while True:
            try:
                await agent.simple_response(random.choice(questions))
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Commentary error: {e}")
                await asyncio.sleep(2)

    @agent.events.subscribe
    async def on_track_added(event: TrackAddedEvent):
        """Start streaming and commentary when video track is added."""
        nonlocal commentary_task, video_task

        # Only trigger on VIDEO track (track_type 2 = video, 1 = audio)
        if event.track_type != 2:
            return

        logger.info("Video track detected - starting commentary")

        if streamer and video_task is None:
            video_task = asyncio.create_task(streamer.stream_to_gemini(agent))
            logger.info(f"Started audio streaming to Gemini from {video_path}")

        if commentary_task is None:
            logger.info("Starting commentary loop")
            commentary_task = asyncio.create_task(run_commentary())

    return agent


async def join_call(agent: Agent, call_type: str, call_id: str, **kwargs) -> None:
    call = await agent.create_call(call_type, call_id)
    async with agent.join(call):
        await agent.finish()


if __name__ == "__main__":
    import sys
    import click

    if len(sys.argv) > 1 and sys.argv[1] == "start":
        @click.command()
        @click.option("--fav-team", "-f", default="", help="Your favorite team name")
        @click.option("--team1", default="Green Bay Packers", help="Team 1 name")
        @click.option("--team2", default="Chicago Bears", help="Team 2 name")
        @click.option("--level", "-l", type=click.Choice(["beginner", "intermediate", "expert"]), default="beginner", help="Knowledge level")
        @click.option("--style", "-s", type=click.Choice(["enthusiastic", "analytical", "casual", "roasting"]), default="enthusiastic", help="Commentary style")
        @click.option("--video", "-v", required=True, help="Video file path")
        def start(fav_team: str, team1: str, team2: str, level: str, style: str, video: str):
            """Start the personalized football commentator."""
            os.environ["FAV_TEAM_NAME"] = fav_team
            os.environ["TEAM1_NAME"] = team1
            os.environ["TEAM2_NAME"] = team2
            os.environ["KNOWLEDGE_LEVEL"] = level
            os.environ["COMMENTARY_STYLE"] = style
            os.environ["VIDEO_PATH"] = video

            click.echo("🎙️  Personalized Football Commentator")
            click.echo(f"   Video: {video}")
            click.echo(f"   Favorite Team: {fav_team if fav_team else 'None'}")
            click.echo(f"   Teams: {team1} vs {team2}")
            click.echo(f"   Knowledge Level: {level}")
            click.echo(f"   Style: {style}")
            click.echo()

            # Run without --video-track-override since we handle it ourselves
            sys.argv = [sys.argv[0], "run"]
            Runner(AgentLauncher(create_agent=create_agent, join_call=join_call)).cli()

        sys.argv.pop(1)
        start()
    else:
        Runner(AgentLauncher(create_agent=create_agent, join_call=join_call)).cli()
