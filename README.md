# DARE

Backend for DARE

## Initial Setup

Please do the following to get this project up and running locally: 

Clone the repo
```
git clone https://github.com/cmudco/dare-backend.git
```

Make virtual environment (with python 3.11) in .venv in the project directory and activate it
```
python3 -m venv .venv
source .venv/bin/activate
```

Install the dev requirements
```
pip install -r requirements/local.txt
```

Create `.env` next to `.example.env` and set the env variables.
For dev/local setup, these can be the values of the following env variables:
```
DJANGO_SETTINGS_MODULE=config.settings.local
DJANGO_DEBUG=True
```

**Note:** 
For local development, we are using SQLite as the database, so you don't need to set up PostgreSQL or configure any database credentials.
For production, create postgreSQL database locally and set the DB env variables in `.env` you just created.

Run migrations
```
python manage.py migrate
```
Run the project 
```bash
python manage.py runserver
```

## Development Workflow

1. **Feature Branch Creation**: 
   - When working on a feature, fix, refactor, or any other task, create a new feature branch.
   - Naming convention: `[YourName]/[Feature/Fix/Refactor]/[Description]`.
   - Once completed, merge your feature branch into the `dev` branch.

2. **Commit Messages**:
   - Ensure your commit messages are descriptive and explanatory.
   - These messages will be used to generate release notes.

3. **Pull Request (PR) Creation**:
   - Link your issue ticket in the development section of the PR.
   - Attach screenshots of your work, if available.
   - Describe what the PR does, how it can be manually tested, and request a review from another developer.
   - Once approved and merged into `dev`, the associated issue ticket will automatically move to the "in progress done" column on the board.

## Best Practices:

- Always wrap user-facing strings with the translation function.
- Preferably, source all strings from a constants file.
- Do **NOT** translate any exceptions or errors that are logged.

## Sending Emails From App

To send emails from your local setup then set the following env variables in your `.env` file:
```
EMAIL_HOST='********'
EMAIL_HOST_USER='********'
EMAIL_HOST_PASSWORD='*************'
EMAIL_FROM='*******'
```


## Formatting and Imports Sorting

Currently, we are using [black](https://pypi.org/project/black/) for formatting and [isort](https://pypi.org/project/isort/) for import sorting.

**(Make sure your environment is activated before you run these commands.)**

To check files formatting etc, run
```
black --check --verbose .
```

To fix files formatting, run
```
black .
```

To check imports sorting, run
```
isort . -c
```

To fix imports sorting, run
```
isort .
```

## Configurable Allowed Hosts

Allowed hosts are now configurable via an environment variable. By default, the application allows `dare`. Additional domains can be added as a comma-separated string in the `ALLOWED_HOSTS` environment variable.

Format:

```
ALLOWED_HOSTS='domain1.com,domain2.com'
```

Refer to the `example.env` file for an example configuration.

## Adding a New Variable to the .env File

To add a new variable to the `.env` file and make it accessible throughout the project, follow these steps:

1. Add the new variable to the `example.env` file:
   - Use uppercase letters for the variable name and replace spaces with underscores.
   - Follow the standard format for `.env` files (e.g., `NEW_VARIABLE_NAME=value`).

2. Add the new variable to your `.env` file in your local development environment.

3. Open the `config/env.py` file and add the following line to declare your new variable:
   NEW_VARIABLE_NAME = os.getenv("NEW_VARIABLE_NAME", default_value)
   Replace `NEW_VARIABLE_NAME` with the name of your new variable and `default_value` with the default value you want to assign (if any).

4. The new variable will now be sourced from the `.env` file and available throughout the entire project using `env.NEW_VARIABLE_NAME`.

5. To use the new variable in a Python file, you can import the `env` object from `config/env.py` and access the variable like this:
   from config.env import env

   # Use the new variable
   print(env.NEW_VARIABLE_NAME)

   Make sure to replace `NEW_VARIABLE_NAME` with the actual name of your new variable.

That's it! You can now use the new variable in your project.

