#include "ros/ros.h"
#include <robotics_function/main_robotics_function.h>

#include <actionlib/client/simple_action_client.h>

#include "ssvep1/GrabAction.h"
#include "ssvep1/MoveAction.h"
#include "ssvep1/ReleaseAction.h"
#include "ssvep1/GrabAndReleaseAction.h"

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <sstream>
#include <iostream>
#include <signal.h>  // Include per la gestione dei segnali

class Interface
{
private:
    ros::NodeHandle nh_;
    ros::Rate INTERFACE_CLOCK_{100};

    actionlib::SimpleActionClient<ssvep1::GrabAction> grabClient_;
    actionlib::SimpleActionClient<ssvep1::MoveAction> moveClient_;
    actionlib::SimpleActionClient<ssvep1::ReleaseAction> releaseClient_;
    actionlib::SimpleActionClient<ssvep1::GrabAndReleaseAction> grabAndReleaseClient_;

    ssvep1::MoveGoal MOVE_GOAL_;
    ssvep1::GrabGoal GRAB_GOAL_;
    ssvep1::ReleaseGoal RELEASE_GOAL_;
    ssvep1::GrabAndReleaseGoal GRAB_AND_REL_GOAL_;

    std::string INSTRUCTION_;
    std::string INSTR_TYPE_;
    std::vector<std::string> INSTR_COMPS_;

    int server_fd, new_socket;
    struct sockaddr_in address;
    int addrlen = sizeof(address);

    bool stop_server_ = false;  // Nuova variabile per gestire il loop principale

    std::vector<std::string> splitString(std::string input)
    {
        std::vector<std::string> result;
        std::istringstream iss(input);
        std::string segment;

        while (std::getline(iss, segment, '|'))
        {
            std::istringstream segmentStream(segment);
            std::string element;

            while (std::getline(segmentStream, element, ','))
            {
                result.push_back(element);
            }
        }

        return result;
    }

    // Funzione per acquisire l'istruzione tramite TCP/IP
    std::string acquireInstruction()
    {
        char buffer[1024] = {0};
        int valread = read(new_socket, buffer, 1024);
        if (valread <= 0)
        {
            ROS_ERROR("Errore nella ricezione dell'istruzione.");
            return "";
        }
        std::string instruction(buffer);
        return instruction;
    }

    // Funzione per inviare un messaggio di conferma al client
    void sendAcknowledgment(const std::string &message)
    {
        send(new_socket, message.c_str(), message.length(), 0);
    }

    // Funzione che gestisce la chiusura della connessione e ripristina il server in ascolto
    void resetConnection()
    {
        close(new_socket);  // Chiudiamo il socket corrente
        ROS_INFO("Connessione chiusa. In attesa di nuove connessioni...");

        if ((new_socket = accept(server_fd, (struct sockaddr *)&address, (socklen_t *)&addrlen)) < 0)
        {
            ROS_ERROR("Errore nell'accettare la connessione.");
            exit(EXIT_FAILURE);
        }

        system("clear");
        ROS_INFO("Nuova connessione stabilita.");
    }

public:
    Interface() :
        moveClient_("Move", true),
        grabClient_("Grab", true),
        releaseClient_("Release", true),
        grabAndReleaseClient_("GrabRel", true)
    {
        // Impostazione del socket TCP/IP
        if ((server_fd = socket(AF_INET, SOCK_STREAM, 0)) == 0)
        {
            ROS_ERROR("Errore nella creazione del socket.");
            exit(EXIT_FAILURE);
        }

        // Impostazione dell'opzione SO_REUSEADDR
        int opt = 1;
        if (setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt)))
        {
            ROS_ERROR("Errore nell'impostazione dell'opzione SO_REUSEADDR.");
            exit(EXIT_FAILURE);
        }

        address.sin_family = AF_INET;
        address.sin_addr.s_addr = INADDR_ANY;
        address.sin_port = htons(8080);

        if (bind(server_fd, (struct sockaddr *)&address, sizeof(address)) < 0)
        {
            ROS_ERROR("Errore nel binding del socket.");
            exit(EXIT_FAILURE);
        }

        if (listen(server_fd, 3) < 0)
        {
            ROS_ERROR("Errore nell'ascolto del socket.");
            exit(EXIT_FAILURE);
        }

        ROS_INFO("In attesa di connessioni...");

        resetConnection();  // Gestisce la prima connessione

        // Gestione del segnale Ctrl+C (SIGINT)
        signal(SIGINT, [](int signum) {
            ROS_INFO("[!] Caught signal: %d. Shutting down...", signum);
            ros::shutdown();  // Chiude in modo sicuro ROS
            exit(0);  // Esce dal programma
        });

        while (ros::ok())
        {
            INSTRUCTION_ = acquireInstruction();
            if (INSTRUCTION_.empty())
            {
                resetConnection();  // Se la connessione si interrompe, torniamo in ascolto
                continue;
            }

            INSTR_COMPS_ = splitString(INSTRUCTION_);
            INSTR_TYPE_ = INSTR_COMPS_[0];

            if (INSTR_TYPE_ == "MOVE")
            {
                MOVE_GOAL_.displacement = {
                    std::stod(INSTR_COMPS_[1]),
                    std::stod(INSTR_COMPS_[2]),
                    std::stod(INSTR_COMPS_[3])};
                MOVE_GOAL_.rpy = {
                    std::stod(INSTR_COMPS_[4]),
                    std::stod(INSTR_COMPS_[5]),
                    std::stod(INSTR_COMPS_[6])};
                MOVE_GOAL_.time = std::stod(INSTR_COMPS_[7]);
                MOVE_GOAL_.velocity = std::stod(INSTR_COMPS_[8]);
                ROS_INFO("Invio del goal MOVE.");
                moveClient_.sendGoal(MOVE_GOAL_);
                ROS_INFO("Attesa dei risultati.");
                moveClient_.waitForResult();
                actionlib::SimpleClientGoalState state = moveClient_.getState();
                ROS_INFO("Azione completata: %s", state.toString().c_str());
                sendAcknowledgment("MOVE_DONE");
            }
            else if (INSTR_TYPE_ == "GRAB")
            {
                GRAB_GOAL_.object_position = {
                    std::stod(INSTR_COMPS_[1]),
                    std::stod(INSTR_COMPS_[2]),
                    std::stod(INSTR_COMPS_[3])};
                ROS_INFO("Invio del goal GRAB.");
                grabClient_.sendGoal(GRAB_GOAL_);
                ROS_INFO("Attesa dei risultati.");
                grabClient_.waitForResult();
                actionlib::SimpleClientGoalState state = grabClient_.getState();
                ROS_INFO("Azione completata: %s", state.toString().c_str());
                sendAcknowledgment("GRAB_DONE");
            }
            else if (INSTR_TYPE_ == "REL")
            {
                RELEASE_GOAL_.release_position = {
                    std::stod(INSTR_COMPS_[1]),
                    std::stod(INSTR_COMPS_[2]),
                    std::stod(INSTR_COMPS_[3])};
                ROS_INFO("Invio del goal RELEASE.");
                releaseClient_.sendGoal(RELEASE_GOAL_);
                ROS_INFO("Attesa dei risultati.");
                releaseClient_.waitForResult();
                actionlib::SimpleClientGoalState state = releaseClient_.getState();
                ROS_INFO("Azione completata: %s", state.toString().c_str());
                sendAcknowledgment("RELEASE_DONE");
            }
            else if (INSTR_TYPE_ == "GRABREL")
            {
                GRAB_AND_REL_GOAL_.object_position = {
                    std::stod(INSTR_COMPS_[1]),
                    std::stod(INSTR_COMPS_[2]),
                    std::stod(INSTR_COMPS_[3])};
                GRAB_AND_REL_GOAL_.release_position = {
                    std::stod(INSTR_COMPS_[4]),
                    std::stod(INSTR_COMPS_[5]),
                    std::stod(INSTR_COMPS_[6])};
                ROS_INFO("Invio del goal GRABREL.");
                grabAndReleaseClient_.sendGoal(GRAB_AND_REL_GOAL_);
                ROS_INFO("Attesa dei risultati.");
                grabAndReleaseClient_.waitForResult();
                actionlib::SimpleClientGoalState state = grabAndReleaseClient_.getState();
                ROS_INFO("Azione completata: %s", state.toString().c_str());
                sendAcknowledgment("GRABREL_DONE");
            }
            else
            {
                ROS_WARN("Istruzione sconosciuta. Riprova.");
            }

            ros::spinOnce();
            INTERFACE_CLOCK_.sleep();
        }
    }

    ~Interface()
    {
        // Chiudi il socket e le connessioni
        close(new_socket);
        close(server_fd);
    }
};

int main(int argc, char **argv)
{
    ros::init(argc, argv, "interface", ros::init_options::NoSigintHandler);

    Interface interfaceNode;

    return 0;
}
