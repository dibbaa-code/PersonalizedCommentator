import logging
import os
import random

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

    fav_color = os.getenv("FAV_JERSEY_COLOR", "")
    level = os.getenv("KNOWLEDGE_LEVEL", "beginner")
    style = os.getenv("COMMENTARY_STYLE", "roasting")

    # Replace placeholders in memory (don't write back)
    instructions = template.replace("{FAV_JERSEY_COLOR}", fav_color if fav_color else "not specified")
    instructions = instructions.replace("{KNOWLEDGE_LEVEL}", level.capitalize())
    instructions = instructions.replace("{COMMENTARY_STYLE}", style.capitalize())

    return instructions


async def create_agent(**kwargs) -> Agent:
    llm = gemini.Realtime()

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

    # Style-based questions
    style = os.getenv("COMMENTARY_STYLE", "enthusiastic")
    if style == "roasting":
        questions = [
            "What's happening? Don't hold back on the roasts.",
            "Who messed up this time?",
            "Give me the play-by-play with your best commentary.",
        ]
    else:
        questions = [
            "Provide an update on the situation on the football field.",
            "What has just happened?",
            "What is happening on the field right now?",
        ]

    # Call LLM once in 4s max
    debouncer = Debouncer(8)

    @agent.events.subscribe
    async def on_detection_completed(event: roboflow.DetectionCompletedEvent):
        """
        Trigger an action when Roboflow detected objects on the video.

        This function will be called for every detection,
        so we use previously created Debouncer object to avoid calling the LLM too often.
        """

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
        @click.option("--color", "-c", default="", help="Your favorite team's jersey color (e.g., 'red', 'blue')")
        @click.option("--level", "-l", type=click.Choice(["beginner", "intermediate", "expert"]), default="intermediate", help="Your football knowledge level")
        @click.option("--style", "-s", type=click.Choice(["enthusiastic", "analytical", "casual", "roasting"]), default="enthusiastic", help="Commentary style")
        @click.option("--video", "-v", default="", help="Video file to use (optional)")
        def start(color: str, level: str, style: str, video: str):
            """Start the personalized football commentator."""
            # Set environment variables (used by get_instructions)
            os.environ["FAV_JERSEY_COLOR"] = color
            os.environ["KNOWLEDGE_LEVEL"] = level
            os.environ["COMMENTARY_STYLE"] = style

            click.echo("🎙️  Personalized Football Commentator")
            click.echo(f"   Favorite Jersey: {color if color else 'None'}")
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
