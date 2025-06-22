from setuptools import setup, find_packages

setup(
    name="proxy-load-balancer",
    version="0.2.0",
    packages=find_packages(include=["proxy_load_balancer*"]),
    install_requires=[
        "aiohttp>=3.8.0",
        "watchdog==6.0.0", 
        "urllib3==2.0.0", 
        "certifi==2024.2.2"
    ],
    python_requires=">=3.8",
    extras_require={
        'test': [
            'pytest==7.4.3',
            'pytest-asyncio==0.21.1',
            'pytest-cov==4.1.0',
            'pytest-timeout==2.2.0',
            'mock==5.1.0',
            'responses==0.24.1'
        ]
    }
)
