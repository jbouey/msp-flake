from setuptools import setup, find_packages

setup(
    name="compliance-agent",
    version="1.0.24",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    package_data={
        "compliance_agent": [
            "web_templates/*.html",
            "rules/*.yaml",
        ],
    },
    include_package_data=True,
    install_requires=[
        "aiohttp>=3.9.0",
        "asyncssh>=2.14.0",
        "cryptography>=41.0.0",
        "pydantic>=2.5.0",
        "pydantic-settings>=2.1.0",
        "fastapi>=0.104.0",
        "uvicorn>=0.24.0",
        "jinja2>=3.1.0",
        "pywinrm>=0.4.3",
        "pyyaml>=6.0.0",
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
            "compliance-agent-appliance=compliance_agent.appliance_agent:main",
            "compliance-web=compliance_agent.web_ui:main",
            "compliance-provision=compliance_agent.provisioning:main",
        ],
    },
    python_requires=">=3.11",
)
