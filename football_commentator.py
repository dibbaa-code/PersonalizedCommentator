import logging
import os
import random
import time

from dotenv import load_dotenv
from utils import Debouncer
from vision_agents.core import Agent, Runner, User
from vision_agents.core.agents import AgentLauncher
from vision_agents.plugins import getstream, gemini, roboflow

logger = logging.getLogger(__name__)

load_dotenv()

# Path to instructions file
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

    # Replace placeholders in memory (don't write back)
    instructions = template.replace("{FAV_TEAM_NAME}", fav_team if fav_team else "not specified")
    instructions = instructions.replace("{KNOWLEDGE_LEVEL}", level.capitalize())
    instructions = instructions.replace("{COMMENTARY_STYLE}", style.capitalize())
    instructions = instructions.replace("{TEAM1_NAME}", team1)
    instructions = instructions.replace("{TEAM2_NAME}", team2)
    instructions = instructions.replace("{TEAM1_COLOR}", team1_color)
    instructions = instructions.replace("{TEAM2_COLOR}", team2_color)

    return instructions


async def create_agent(**kwargs) -> Agent:
    # Configure Gemini for video-only input (no microphone required)
    # Audio output is still enabled for commentary
    llm = gemini.Realtime(
        config={
            "input_audio_transcription": None,  # Disable audio input requirement
        }
    )

    # Get personalized instructions (placeholders replaced in memory)
    instructions = get_instructions()

    agent = Agent(
        edge=getstream.Edge(),  # low latency edge. clients for React, iOS, Android, RN, Flutter etc.
        agent_user=User(name="AI Sports Commentator", id="agent"),
        instructions=instructions,
        processors=[
            roboflow.RoboflowLocalDetectionProcessor(
                classes=["person", "sports ball"],
                conf_threshold=0.5,
                fps=5,
            )
        ],
        llm=llm,
    )

    # Get user preferences for prompts
    style = os.getenv("COMMENTARY_STYLE", "enthusiastic")
    level = os.getenv("KNOWLEDGE_LEVEL", "beginner")

    # Knowledge level reminder to include in prompts
    level_reminder = {
        "beginner": "Remember: explain any football terms simply, the viewer is new to football.",
        "intermediate": "Remember: the viewer understands basic football, explain tactics and formations.",
        "expert": "Remember: use technical jargon (xG, half-spaces, progressive passes) - the viewer is an expert.",
    }.get(level, "")

    # Style-based questions with knowledge level context (2 sentences max)
    brief = "Keep it to 2 sentences max."
    if style == "roasting":
        questions = [
            f"What's happening? Don't hold back on the roasts. {brief} {level_reminder}",
            f"Give me the play-by-play. {brief} {level_reminder}",
        ]
    else:
        questions = [
            f"What's happening on the field? {brief} {level_reminder}",
            f"What just happened? {brief} {level_reminder}",
            f"Quick update on the play. {brief} {level_reminder}",
        ]

    # Call LLM once in 8s max
    debouncer = Debouncer(8)

    # Track if opening commentary has been delivered
    opening_done = False
    opening_time = 0.0  # Timestamp when opening was delivered
    OPENING_COOLDOWN = 20  # Seconds to wait after opening before regular commentary

    # Get team names for opening
    team1 = os.getenv("TEAM1_NAME", "Green Bay Packers")
    team2 = os.getenv("TEAM2_NAME", "Chicago Bears")

    # Opening prompt like real broadcast commentary (kept brief)
    opening_prompt = (
        f"Welcome viewers to {team1} vs {team2}! "
        "Quick broadcast-style intro - who you are and what you see. "
        f"Max 3 sentences. {level_reminder}"
    )

    @agent.events.subscribe
    async def on_detection_completed(event: roboflow.DetectionCompletedEvent):
        """
        Trigger an action when Roboflow detected objects on the video.

        This function will be called for every detection,
        so we use previously created Debouncer object to avoid calling the LLM too often.
        """
        nonlocal opening_done, opening_time

        # Deliver opening commentary on first detection
        if not opening_done:
            opening_done = True
            opening_time = time.time()
            await agent.simple_response(opening_prompt)
            return

        # Wait for opening cooldown before regular commentary
        if time.time() - opening_time < OPENING_COOLDOWN:
            return

        ball_detected = bool(
            [obj for obj in event.objects if obj["label"] == "sports ball"]
        )
        # Ping LLM for a commentary only when the ball is detected and the call is not debounced.
        if ball_detected and debouncer:
            # Pick a question randomly from the list
            await agent.simple_response(random.choice(questions))

    return agent


async def join_call(agent: Agent, call_type: str, call_id: str, **kwargs) -> None:
    call = await agent.create_call(call_type, call_id)

    # Have the agent join the call/room
    async with agent.join(call):
        # run till the call ends
        await agent.finish()


if __name__ == "__main__":
    import sys
    import click

    if len(sys.argv) > 1 and sys.argv[1] == "start":
        @click.command()
        @click.option("--fav-team", "-f", default="", help="Your favorite team name (e.g., 'Green Bay Packers')")
        @click.option("--team1", default="Green Bay Packers", help="Team 1 name (default: Green Bay Packers)")
        @click.option("--team2", default="Chicago Bears", help="Team 2 name (default: Chicago Bears)")
        @click.option("--level", "-l", type=click.Choice(["beginner", "intermediate", "expert"]), default="beginner", help="Your football knowledge level")
        @click.option("--style", "-s", type=click.Choice(["enthusiastic", "analytical", "casual", "roasting"]), default="enthusiastic", help="Commentary style")
        @click.option("--video", "-v", default="", help="Video file to use (optional)")
        def start(fav_team: str, team1: str, team2: str, level: str, style: str, video: str):
            """Start the personalized football commentator."""
            # Set environment variables (used by get_instructions)
            os.environ["FAV_TEAM_NAME"] = fav_team
            os.environ["TEAM1_NAME"] = team1
            os.environ["TEAM2_NAME"] = team2
            os.environ["KNOWLEDGE_LEVEL"] = level
            os.environ["COMMENTARY_STYLE"] = style

            click.echo("🎙️  Personalized Football Commentator")
            click.echo(f"   Favorite Team: {fav_team if fav_team else 'None'}")
            click.echo(f"   Teams: {team1} vs {team2}")
            click.echo(f"   Knowledge Level: {level}")
            click.echo(f"   Style: {style}")
            click.echo()

            # Build run args
            run_args = ["run"]
            if video:
                run_args.extend(["--video-track-override", video])

            sys.argv = [sys.argv[0]] + run_args
            Runner(AgentLauncher(create_agent=create_agent, join_call=join_call)).cli()

        sys.argv.pop(1)
        start()
    else:
        Runner(AgentLauncher(create_agent=create_agent, join_call=join_call)).cli()
