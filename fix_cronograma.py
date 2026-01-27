
import os

file_path = r'c:\Cronograma\core\templates\core\cronograma.html'

try:
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content = content.replace('{ {', '{{')
    
    # Also verify if there are any other specific malformed tags
    # The user reported "expected property name, got '{'". checking for "{ {" is the main fix.

    if content == new_content:
        print("No changes needed (content matches).")
    else:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("Successfully replaced '{ {' with '{{'.")

except Exception as e:
    print(f"Error: {e}")
