import streamlit as st
from pinecone import Pinecone as pin
from langchain.callbacks.manager import CallbackManager
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain.embeddings.ollama import OllamaEmbeddings
from langchain.embeddings.ollama import OllamaEmbeddings
from langchain_ollama import OllamaLLM
from langchain.schema import Document
from dotenv import load_dotenv , find_dotenv
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
import json
import os


load_dotenv(find_dotenv())
PINECONE_HOST = os.getenv("PINECONE_HOST")
PINECONE_KEY=os.getenv("PINECONE_KEY")
LLM=OllamaLLM(model= "llama3.2",
callback_manager=CallbackManager([StreamingStdOutCallbackHandler()]),
temperature=0.6
)


pine=pin(api_key=PINECONE_KEY)
index= pine.Index(host=PINECONE_HOST)
embeddings=OllamaEmbeddings(model = "nomic-embed-text")


def save_dict_to_json(data, filename):
    """Save a dictionary to a JSON file.(for easier access to the chunk text)"""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)


def load_dict_from_json(filename):
    """Load a dictionary from a JSON file.(for easier access to the chunk text)"""
    with open(filename, 'r') as f:
        return json.load(f)
    

def load_document():
    loader = TextLoader("./INSAT_formatted.md")
    document=loader.load()
    return document


def split_text(doc: Document):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=360,
        chunk_overlap=100,
        length_function=len,
        add_start_index=True,
    )
    chunks = text_splitter.split_documents(doc)
    print(f"Split {len(doc)} documents into {len(chunks)} chunks.")
    heading_hierarchy = {
        1: "",  # Level 1 heading
        2: "",  # Level 2 heading
        3: ""   # Level 3 heading
    }
    
    # Process chunks and add titles
    titled_chunks = []
    for chunk in chunks:
        lines = chunk.page_content.split("\n")
        for line in lines:
            if line.startswith("# "):  # Level 1
                heading_hierarchy[1] = line.strip("# ").strip()
                # Clear lower level headings when we find a new level 1
                heading_hierarchy[2] = ""
                heading_hierarchy[3] = ""
            elif line.startswith("## "):  # Level 2
                heading_hierarchy[2] = line.strip("# ").strip()
                # Clear lower level headings when we find a new level 2
                heading_hierarchy[3] = ""
            elif line.startswith("### "):  # Level 3
                heading_hierarchy[3] = line.strip("# ").strip()
        
        # Build the title path using only non-empty heading levels
        title_parts = []
        for level in range(1, 4):
            if heading_hierarchy[level]:
                title_parts.append(heading_hierarchy[level])
        
        current_title = " > ".join(title_parts) if title_parts else ""
        
        # Add the title to the chunk's metadata
        chunk.metadata["title"] = current_title
        titled_chunks.append(chunk)
    if titled_chunks:
        example_chunk = titled_chunks[0]
        print(f"Example chunk content: {example_chunk.page_content}")
        print(f"Example chunk title: {example_chunk.metadata['title']}")

    return titled_chunks


def save_chunks_as_dict (chunks : list[Document]) -> dict:
    #create a dictionnary with the metadata.start_index and the page content of each chunk for access to text data
    chunk_dict = {}
    for chunk in chunks:
        start_index = chunk.metadata.get("start_index")  # Get the start_index from metadata
        if start_index is not None:
            chunk_dict[str(start_index)] = chunk.page_content  # Map start_index to page_content
        else:
            print(f"Warning: Chunk missing 'start_index' in metadata: {chunk.metadata}")
    return chunk_dict


        
def embed_and_save(chunks : list[Document]):

    for chunk in chunks:
        index.upsert([{
            "id":str( chunk.metadata["start_index"]),
            "values": embeddings.embed_query(chunk.page_content)
        }])


def query_database(query:str):
    query_embedding = embeddings.embed_query(query)
    ranked_results = index.query(
        namespace="", 
        vector=query_embedding,
        top_k=15,
        include_metadata= True
    )
   # Filter results by threshold
    filtered_results = [item for item in ranked_results.matches if item.score >= 0.7]
    
    # Ensure at least `min_items` are returned
    if len(filtered_results) < 5:
        # Fallback to the top `min_items` if filtering removes too many items
        filtered_results = ranked_results.matches[:5]
    
    # For debugging
    for item in filtered_results:
        print(item.id)
    
    # Create text list from dictionary and append metadata titles
    text_list = []
    for item in filtered_results:
        # Get the text from dictionary using the id
        text_content = dic[item.id]
        
        try:
            # Get the title from metadata, safely handling missing keys
            metadata_title = item.metadata.get('title', '')
            
            # Combine metadata title with text from dictionary
            if metadata_title:
                formatted_text = f"{metadata_title}:\n\n{text_content}"
            else:
                formatted_text = text_content
                
            text_list.append(formatted_text)
        except Exception as e:
            # Fallback if there's any issue with metadata
            print(f"Error processing metadata for {item.id}: {e}")
            text_list.append(text_content)
    
    print(text_list)
    return text_list


def update_with_metadata(chunks : list[Document]):
    for chunk in chunks:
        index.update(
        id=str( chunk.metadata["start_index"]), 
        set_metadata={"title": str(chunk.metadata['title'])}, 
    
)
        

def generate_responses(query : str):
    pre_promt = f"""
    [INSTRUCTIONS]
    - Analysez cette question et extrayez seulement les mots-clés essentiels: {query}
    - Exclure: formules de politesse ("Mr", "Mme", "s'il vous plaît")
    - Exclure: verbes et mots vides (être, avoir, comment, pourquoi)
    - Formater: Liste de mots-clés séparés par des espaces
    - Ne pas inclure de texte supplémentaire
    
    [EXEMPLE]
    Question: "Comment s'inscrire au club robotique ?"
    Réponse: inscription club robotique
    """
    new_prompt = LLM.invoke(pre_promt)
    print (new_prompt)
    db_responses=query_database(new_prompt)
    context = "\n".join(db_responses) if db_responses else "Aucun contexte disponible."
    prompt= f"""
    [ROLE]
    Assistant spécialisé de l'INSAT
    
    [TÂCHE]
    Répondre précisément à la question étudiante en utilisant strictement le contexte fourni
    
    [DIRECTIVES]
    1. Réponse concise (1-2 phrases maximum)
    2. Si le contexte est insuffisant, répondre "Je n'ai pas trouvé d'information précise à ce sujet"
    3. Ne pas inventer d'informations
    4. Structurer la réponse:
       - D'abord la réponse directe
       - Puis la source si disponible (ex: "Source: Réglement des clubs")
    
    [QUESTION]
    {query}
    
    [CONTEXTE]
    {context}
    """
    LLM_response=LLM.invoke(prompt)
    return (LLM_response)



def show_messages():
    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display chat messages from history on app rerun
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])


    # React to user input
    if prompt := st.chat_input("Salut ! Demande-moi à propos de l'INSAT !"):
        # Display user message in chat message container
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})    # Add user message to chat history

    
        response = generate_responses(prompt)
    # Display assistant response in chat message container
        with st.chat_message("assistant"):
            st.markdown(response)
        st.session_state.messages.append({"role": "assistant", "content": response})# Add assistant response to chat history





if __name__ == '__main__':
    try:
        # Check if data file exists and is valid
        try:
            dic = load_dict_from_json("new_data.json")
            if not dic:  # If file exists but is empty
                raise FileNotFoundError
        except (FileNotFoundError, json.JSONDecodeError):
            # Process documents if data file doesn't exist or is invalid
            doc = load_document()
            chunks = split_text(doc)   
            dic = save_chunks_as_dict(chunks)
            save_dict_to_json(dic, "new_data.json")
            embed_and_save(chunks)
            update_with_metadata(chunks)  # Fixed function name to match your code
        
        # Start the Streamlit app
        show_messages()
        
    except Exception as e:
        st.error(f"Application failed to start: {str(e)}")
        # Optionally log the full error
        import traceback
        traceback.print_exc()

