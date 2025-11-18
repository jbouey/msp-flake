from setuptools import setup, find_packages

setup(
    name="compliance-agent",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "aiohttp>=3.9.0",
        "cryptography>=41.0.0",
        "pydantic>=2.5.0",
        "pydantic-settings>=2.1.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-cov>=4.1.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "compliance-agent=compliance_agent.agent:main",
        ],
    },
    python_requires=">=3.11",
)
