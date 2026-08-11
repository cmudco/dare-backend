"""The pure half of the memory system.

No storage, no network, no clock that was not passed in as an argument. These
modules are a 1:1 port of the reference prototype's pure core
(memory-explore/memory-app/lib/memory) and are where the test suite points;
everything that touches Django models or OpenAI lives in memory/services/.
"""
