"""The verification sandbox: runs repository checks on a proposed change, isolated from the service.

It runs as its **own container** (``docker compose`` service ``sandbox``) on an
internal network without internet access, and executes every check as an
unprivileged user in a fresh workspace that is wiped after each job. The
AutoForge service talks to it over HTTP (``integrations/sandbox``).

Standard library only, so the sandbox image carries the build tools but none of
the service's dependencies or secrets:

- ``protocol`` - request/response format shared with the service-side client;
- ``runner``   - one job: unpack the repository, apply the change, run the checks;
- ``server``   - HTTP front end that starts each job as the unprivileged user.
"""
