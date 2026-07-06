import asyncio
from ai.manuscript_generation import generate_section
import ai.llm_provider

# patch to intercept and print
orig_generate = ai.llm_provider.generate_completion
call_count = {'gemini': 0, 'groq': 0, 'other': 0}

async def mock_generate_completion(*args, **kwargs):
    provider = kwargs.get('provider_override')
    if provider == 'gemini': call_count['gemini'] += 1
    elif provider == 'groq': call_count['groq'] += 1
    else: call_count['other'] += 1
    
    if provider == 'groq':
        return '{"well_covered": ["mock covered"], "gaps": ["mock gap"], "suggested_direction": "mock direction string that is long enough to bypass vagueness check. mock direction string that is long enough to bypass vagueness check."}'
    return 'Mock generated content.'

ai.llm_provider.generate_completion = mock_generate_completion

async def main():
    print('Testing lit_review...')
    content, flags = await generate_section('quantum computing in finance', 'lit_review', 'Use recent trends')
    
    print('Content:', content)
    print('Gap Analysis flags present:', 'gap_analysis' in flags)
    print('Call counts:', call_count)

asyncio.run(main())
