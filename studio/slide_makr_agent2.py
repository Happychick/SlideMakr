from typing_extensions import TypedDict
from google.oauth2.service_account import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import Resource, build
import re
import json
import os

# Imports for building the graph
from langgraph.graph import StateGraph, START, END
from langgraph.graph import MessagesState
from langgraph.prebuilt import ToolNode

from typing import Annotated, Optional, List, Dict, Any, Tuple, TypedDict, Sequence
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, AnyMessage, ToolMessage
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# At the top of your file, import dotenv
from dotenv import load_dotenv
# Then load the variables from .env
load_dotenv()
service_account_path = os.getenv("SERVICE_ACCOUNT_PATH")

# 1. Set the model
llm = ChatOpenAI(model="gpt-4", temperature=0, max_tokens=2048)

# 2. Define the class which includes all the functions I want to use
class Slidemakr:
    def __init__(self, service_account_path):
        SCOPES = [
            'https://www.googleapis.com/auth/presentations',
            'https://www.googleapis.com/auth/drive'
        ]
        self.credentials = service_account.Credentials.from_service_account_file(
            service_account_path, scopes=SCOPES
        )
        self.slides_service = build('slides', 'v1', credentials=self.credentials)
        self.drive_service = build('drive', 'v3', credentials=self.credentials)

    def generate_code(self, instructions: str) -> str:
        """Generates code based on human instructions for Google Slides."""
        messages = [
            SystemMessage(content="""You are an engineer, create a list of requests in python code that makes the content of a
                        Google slides presentation from the human instructions.
                        The code will be used as content for requests in another function where we call the Google API so in your response start immediately with the code like this: ['{
                        'createSlide':'. Do not include the 'request = []', or any text, like '''json, just the list.
                        Please format the output as valid JSON
                        with double quotes for all property names and string values.
                        Every item in the request list should be formatted as a dictionary of dictionaries, like this {{}}.
                        Here is an example of a request item for createSlide: {
                          "createSlide": {
                              "objectId": f"slide_{len(requests)}",
                              "slideLayoutReference": {
                                  "predefinedLayout": slide_info.get("slideType", "BLANK")
                              }
                          }
                      } Thank you!"""),
            HumanMessage(content=instructions)
        ]
        response = llm.invoke(messages)
        generated_code = response.content
        cleaned_code = re.sub(r'^```python\n|```$', '', generated_code, flags=re.MULTILINE)
        return cleaned_code

    def create_presentation(self) -> str:
        """Creates a new Google Slides presentation."""
        presentation = self.slides_service.presentations().create(
            body={'title': 'Your Presentation'}
        ).execute()
        presentation_id = presentation['presentationId']
        return presentation_id

    def run_generated_code(self, generated_code: str, presentation_id: str) -> str:
        """Executes the generated code to update the presentation."""
        try:
            requests = json.loads(generated_code)
        except json.JSONDecodeError as e:
            return json.dumps({"status": "error", "json_error": str(e)})

        errors = {}
        for index, req in enumerate(requests):
            try:
                self.slides_service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={'requests': [req]}
                ).execute()
            except Exception as e:
                errors[f"request_{index}"] = str(e)
        
        url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'
        
        if errors:
            return json.dumps({"status": "error", "url": url, "errors": errors})
        else:
            return json.dumps({"status": "success", "url": url})

    def share_presentation(self, presentation_id: str, email: str) -> dict:
        """Shares the presentation with a given email address."""
        try:
            self.drive_service.permissions().create(
                fileId=presentation_id,
                body={'type': 'user', 'role': 'writer', 'emailAddress': email},
                fields='id'
            ).execute()
            return {"status": "shared", "email": email}
        except Exception as e:
            return {"status": "error", "error": str(e)}

# 3. Create tools based on the Slidemakr class
slide_tools = Slidemakr(service_account_path)

@tool
def generate_code_tool(instructions: str) -> str:
    "Creates the python code needed to build the slides"
    return slide_tools.generate_code(instructions)

@tool
def create_presentation_tool() -> str:
    "Creates a new presentation and returns the presentation ID"
    return slide_tools.create_presentation()

@tool
def run_generated_code_tool(generated_code: str, presentation_id: str) -> str:
    "Executes the code to build the slides in the presentation"
    return slide_tools.run_generated_code(generated_code, presentation_id)

# 4. Define our state
class SlideMakrState(MessagesState):
    generated_code: Optional[str] = None
    presentation_id: Optional[str] = None
    url: Optional[str] = None
    email: Optional[str] = None
    execution_status: Optional[str] = None
    has_errors: bool = False

# 5. Define the system prompt for our LLM Agent
AGENT_SYSTEM_PROMPT = """You are an engineer that creates Google Slides presentations based on user instructions.
Your task is to:
1. Generate the code for the presentation based on user instructions using the generate_code_tool
2. Create a new presentation using the create_presentation_tool, if one doesn't exist already
3. Run the generated code to build the presentation using the run_generated_code_tool with the generated code and presentation ID
4. If there are errors, fix the code and re-run the section that had errors
5. When successful, ask for the user's email to share the presentation

IMPORTANT: Always follow this exact sequence of steps in order. Do not skip any steps.
"""

# 6. Define the nodes for our graph
def llm_with_tools_node(state: SlideMakrState) -> Dict:
    """Use the LLM to decide what to do next based on the conversation history."""
    messages = state["messages"]
    
    # Add system message if needed
    if not any(isinstance(msg, SystemMessage) for msg in messages):
        messages.insert(0, SystemMessage(content=AGENT_SYSTEM_PROMPT))
    
    # Get a response from the LLM with tool-calling capabilities
    response = llm.bind_tools([
        generate_code_tool,
        create_presentation_tool,
        run_generated_code_tool
    ]).invoke(messages)
    
    return {"messages": state["messages"] + [response]}

def process_last_tool_results(state: SlideMakrState) -> Dict:
    """Process the results of the last tool execution and update the state."""
    # Initialize with current state
    result_state = {
        "messages": state["messages"],
        "generated_code": state.get("generated_code"),
        "presentation_id": state.get("presentation_id"),
        "url": state.get("url"),
        "execution_status": state.get("execution_status"),
        "has_errors": state.get("has_errors", False)
    }
    
    # Find the most recent tool messages
    messages = state["messages"]
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        
        # Process tool messages
        if isinstance(message, ToolMessage):
            tool_name = message.name
            content = message.content
            
            if tool_name == "generate_code_tool":
                result_state["generated_code"] = content
            
            elif tool_name == "create_presentation_tool":
                result_state["presentation_id"] = content
            
            elif tool_name == "run_generated_code_tool":
                try:
                    output = json.loads(content)
                    result_state["execution_status"] = output.get("status")
                    if "url" in output:
                        result_state["url"] = output["url"]
                    result_state["has_errors"] = output.get("status") == "error"
                except:
                    result_state["has_errors"] = True
    
    return result_state

def get_email_node(state: SlideMakrState) -> Dict:
    """Ask the user for their email to share the presentation."""
    email = interrupt(
        {"message": "The presentation has been created successfully! Please provide your email address to share it with you."}
    )
    return {
        "email": email,
        "messages": state["messages"] + [HumanMessage(content=f"My email is: {email}")]
    }

def share_presentation_node(state: SlideMakrState) -> Dict:
    """Share the presentation with the user's email."""
    email = state.get("email")
    presentation_id = state.get("presentation_id")
    
    # Share the presentation
    result = slide_tools.share_presentation(presentation_id, email)
    
    # Create a message based on the result
    if result.get("status") == "shared":
        message = AIMessage(content=f"Great! I've shared the presentation with {email}. You can access it at {state.get('url')}.")
    else:
        message = AIMessage(content=f"I encountered an error while trying to share the presentation: {result.get('error', 'Unknown error')}")
    
    return {"messages": state["messages"] + [message]}

# 7. Define routing functions
def route_based_on_tool_call(state: SlideMakrState) -> str:
    """Determine if we should execute tools."""
    last_message = state["messages"][-1]
    
    if isinstance(last_message, AIMessage) and hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    
    # Check if presentation is successful and we need the email
    if state.get("execution_status") == "success" and state.get("url") and not state.get("email"):
        return "get_email"
    
    # Otherwise continue with the LLM
    return "llm"

def route_after_tools(state: SlideMakrState) -> str:
    """Determine what to do after tools are executed."""
    # If we've successfully created a presentation and have a URL
    if state.get("execution_status") == "success" and state.get("url"):
        return "get_email"
    
    # Otherwise continue with the LLM (to fix errors if needed)
    return "llm"

def should_share(state: SlideMakrState) -> str:
    """Determine if we should share the presentation."""
    # If we have an email and a presentation ID, we can share
    if state.get("email") and state.get("presentation_id"):
        return "share"
    
    # Otherwise back to the LLM for further processing
    return "llm"

# 8. Build the graph
builder = StateGraph(SlideMakrState)

# Add nodes
builder.add_node("llm", llm_with_tools_node)
builder.add_node("tools", ToolNode([generate_code_tool, create_presentation_tool, run_generated_code_tool]))
builder.add_node("process_results", process_last_tool_results)
builder.add_node("get_email", get_email_node)
builder.add_node("share", share_presentation_node)

# Add edges
builder.add_edge(START, "llm")
builder.add_conditional_edges("llm", route_based_on_tool_call)
builder.add_edge("tools", "process_results")
builder.add_conditional_edges("process_results", route_after_tools)
builder.add_conditional_edges("get_email", should_share)
builder.add_edge("share", END)

# Compile the graph
graph = builder.compile()

# Example usage:
# async def run_slidemakr_agent(instructions: str):
#     inputs = {"messages": [HumanMessage(content=instructions)]}
#     for output in graph.stream(inputs):
#         if "__end__" in output:
#             return output
#         # Handle interrupt for email collection if needed