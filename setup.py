import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="tstriage",
    version="0.0.9",
    author="poke30744",
    author_email="poke30744@gmail.com",
    description="MEPG TS Triage Runner",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/pypa/sampleproject",
    packages=setuptools.find_packages(),
    install_requires=[
        'tscutter',
        'tsmarker',
        'watchdog',
        'py-getch',
        'yaml'
    ],
    package_data={'': ['channels.yml']},
    include_package_data=True,
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.9',
)
