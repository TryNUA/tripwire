"""File saved tripwire reports as Linear issues over the GraphQL API.

Stdlib-only (urllib), like ``status``/``save``: works anywhere the CLI does.
Auth is a personal API key in ``LINEAR_API_KEY``
(https://linear.app/settings/account/security — "New API key").
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

API_URL = "https://api.linear.app/graphql"

_TEAMS_QUERY = "query { teams { nodes { id key name } } }"
_CREATE_MUTATION = """
mutation($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { identifier url } }
}
"""


class LinearError(RuntimeError):
    """The Linear API refused or the network failed; message is user-facing."""


def _post(query: str, variables: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise LinearError(f"Linear API returned HTTP {exc.code}: {detail}") from exc
    except OSError as exc:
        raise LinearError(f"could not reach Linear: {exc}") from exc
    if body.get("errors"):
        raise LinearError(body["errors"][0].get("message", "Linear API error"))
    return body["data"]


def resolve_team_id(api_key: str, team: str = "") -> str:
    """Return the team id for a key/name, or the sole team if none given."""
    teams = _post(_TEAMS_QUERY, {}, api_key)["teams"]["nodes"]
    if not teams:
        raise LinearError("no Linear teams visible to this API key")
    if team:
        for node in teams:
            if team.lower() in (node["key"].lower(), node["name"].lower()):
                return node["id"]
        raise LinearError(
            f"no Linear team matching {team!r} — teams: " + ", ".join(node["key"] for node in teams)
        )
    if len(teams) == 1:
        return teams[0]["id"]
    raise LinearError(
        "multiple Linear teams — pass --team with one of: "
        + ", ".join(node["key"] for node in teams)
    )


def create_issue(api_key: str, team_id: str, title: str, description: str) -> tuple[str, str]:
    """Create the issue; return (identifier, url) like ("ENG-42", "https://…")."""
    variables = {"input": {"teamId": team_id, "title": title, "description": description}}
    result = _post(_CREATE_MUTATION, variables, api_key)["issueCreate"]
    if not result.get("success") or not result.get("issue"):
        raise LinearError("Linear did not create the issue")
    return result["issue"]["identifier"], result["issue"]["url"]
