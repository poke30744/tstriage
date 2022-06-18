import setuptools, os

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

build_number = os.getenv('BUILD_NUMBER') or '0'

setuptools.setup(
    name="tstriage",
    version=f"0.1.{build_number}",
    author="poke30744",
    author_email="poke30744@gmail.com",
    description="MEPG TS Triage Runner",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/poke30744/tstriage/blob/main/README.md",
    packages=setuptools.find_packages(exclude=['tests',]),
    install_requires=[
        'tscutter',
        'tsmarker',
        'PyYAML'
    ],
    package_data={'': ['channels.yml']},
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'tstriage=tstriage.runner:main',
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.9',
)
